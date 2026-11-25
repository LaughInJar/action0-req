"""Streaming abstractions for request and response bodies."""

import asyncio
import functools
import io
import itertools
import os
import sys
import tempfile
from types import TracebackType
from typing import IO
from typing import AsyncIterable
from typing import AsyncIterator
from typing import Awaitable
from typing import Callable
from typing import Iterable
from typing import Iterator
from typing import ParamSpec
from typing import Protocol
from typing import TypeVar
from typing import Union
from typing import cast
from typing import runtime_checkable

from .headers import Header
from .headers import Headers


@runtime_checkable
class BodyProducer(Protocol):
    """
    The interface for streaming the bytes of a request or response body.

    Implementations produce the body as a sequence of chunks — synchronously
    via :py:meth:`chunks` or asynchronously via :py:meth:`achunks` — and as a
    whole via :py:meth:`as_bytes`. :py:class:`BytesBody` is the simplest
    implementation; :py:class:`FileBody`, :py:class:`IterableBody`,
    :py:class:`AsyncIterableBody` and :py:class:`SpooledBody` cover files,
    streams and spooled data.

    The protocol is ``runtime_checkable``, so ``isinstance(obj, BodyProducer)``
    checks that the four methods exist.

    A producer holding a resource (an open connection, a temporary file)
    additionally offers ``close()`` and/or ``aclose()``. They are not part
    of the protocol, so simple producers need not implement them;
    :py:meth:`Response.close() <action0.req.response.Response.close>` and
    :py:meth:`~action0.req.response.Response.aclose` call them if present.
    """

    def content_length(self) -> Union[int, None]:
        """
        :return: the total number of body bytes, or ``None`` if not known in
                 advance (such a body would be sent chunked)
        """
        ...

    def chunks(self) -> Iterator[bytes]:
        """
        :return: the body as an iterator of byte chunks
        """
        ...

    def achunks(self) -> AsyncIterator[bytes]:
        """
        :return: the body as an asynchronous iterator of byte chunks
        """
        ...

    def as_bytes(self) -> bytes:
        """
        :return: the whole body as a single bytes object
        """
        ...


class BytesBody:
    """
    The simplest :py:class:`BodyProducer`: an in-memory bytes value,
    produced as a single chunk.

    Example::

        >>> body = BytesBody(b"hello")
        >>> body.content_length()
        5
        >>> list(body.chunks())
        [b'hello']
        >>> body.as_bytes()
        b'hello'
    """

    def __init__(self, data: bytes) -> None:
        """
        :param data: the body bytes
        """
        self._data = data

    def content_length(self) -> Union[int, None]:
        """
        :return: the number of body bytes
        """
        return len(self._data)

    def chunks(self) -> Iterator[bytes]:
        """
        :return: the body as an iterator with a single chunk
        """
        yield self._data

    async def achunks(self) -> AsyncIterator[bytes]:
        """
        :return: the body as an asynchronous iterator with a single chunk
        """
        yield self._data

    def as_bytes(self) -> bytes:
        """
        :return: the body bytes
        """
        return self._data


_P = ParamSpec("_P")
_T = TypeVar("_T")

#: how many bytes :py:func:`asave_body` collects before handing them to a
#: worker thread — bounds the number of thread hops for a body arriving
#: in many small chunks
_WRITE_BATCH = 65536


async def _in_thread(function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """
    Run a blocking function in a worker thread, without stalling the
    event loop and without any extra dependency.

    On asyncio (also below anyio) this is :py:func:`asyncio.to_thread`.
    When there is no running asyncio loop but trio has been imported, the
    caller must be a trio task, so trio's own thread pool is used.

    :param function: the blocking function
    :param args: its positional arguments
    :param kwargs: its keyword arguments
    :return: what the function returned
    :raises RuntimeError: if called outside of asyncio and trio
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # looked up instead of imported: trio is not a dependency, and
        # code running below trio has necessarily imported it already
        trio = sys.modules.get("trio")
        if trio is None:
            raise
        call = functools.partial(function, *args, **kwargs)
        return cast("_T", await trio.to_thread.run_sync(call))
    return await asyncio.to_thread(function, *args, **kwargs)


async def _in_thread_shielded(
    function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs
) -> _T:
    """
    :py:func:`_in_thread` for cleanup that must not be skipped — closing
    a file, writing out what has arrived: the function runs to its end
    even if the calling task is cancelled (a timeout, a failing sibling
    task). The cancellation is not swallowed, only postponed: it is
    raised once the function has returned.

    A cleanup typically runs *because* of a cancellation. trio then
    refuses to start anything in the cancelled scope, and so does anyio
    on asyncio, which keeps cancelling a task until it has left the
    scope.

    :param function: the blocking function
    :param args: its positional arguments
    :param kwargs: its keyword arguments
    :return: what the function returned
    :raises RuntimeError: if called outside of asyncio and trio
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        trio = sys.modules.get("trio")
        if trio is None:
            raise
        call = functools.partial(function, *args, **kwargs)
        # the pending cancellation is delivered at the first checkpoint
        # after the shielded scope
        with trio.CancelScope(shield=True):
            return cast("_T", await trio.to_thread.run_sync(call))
    future = asyncio.ensure_future(asyncio.to_thread(function, *args, **kwargs))
    cancelled: Union[asyncio.CancelledError, None] = None
    while not future.done():
        try:
            # shield() keeps the cancellation of this task away from the
            # future; waiting again is what makes the function finish
            # before anything else happens to the file it works on
            await asyncio.shield(future)
        except asyncio.CancelledError as error:
            cancelled = error
        except Exception:
            # the function's own error: raised by result() below
            pass
    if cancelled is not None:
        raise cancelled
    return future.result()


def _open_binary(path: Union[str, "os.PathLike[str]"], mode: str = "rb") -> IO[bytes]:
    """
    Open a file in a binary mode.

    A typed wrapper: passing the built-in ``open`` through a worker
    thread loses the overload selection and with it the precise
    ``IO[bytes]`` return type.

    :param path: the path of the file to open
    :param mode: the (binary) mode to open the file in
    :return: the opened binary file object
    """
    return cast("IO[bytes]", open(path, mode))


class FileBody:
    """
    A :py:class:`BodyProducer` streaming a file in chunks.

    The source can be a path or an already-open binary file object:

    - A path is opened freshly for every iteration, so the body is
      re-iterable (e.g. for retries) and no file descriptor is held
      between uses.
    - A file object is used as-is and never closed by this class. If it
      is seekable it is rewound to the start for every iteration (making
      it re-iterable too); if not, reading starts at the current position
      and the body is consumable only once.

    :py:meth:`achunks` performs every blocking file operation in a worker
    thread (:py:func:`asyncio.to_thread`, or trio's thread pool when
    running below trio), so the event loop is never stalled — without any
    extra dependency.
    """

    def __init__(
        self,
        source: Union[str, "os.PathLike[str]", IO[bytes]],
        chunk_size: int = 65536,
    ) -> None:
        """
        :param source: the path of the file to stream, or an open binary
                       file object
        :param chunk_size: the number of bytes per chunk
        :raises ValueError: if the chunk size is not positive
        """
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")
        if isinstance(source, (str, os.PathLike)):
            self._path: Union[str, "os.PathLike[str]", None] = source
            self._file: Union[IO[bytes], None] = None
        else:
            self._path = None
            self._file = source
        self._chunk_size = chunk_size

    def content_length(self) -> Union[int, None]:
        """
        :return: the size of the file; for a non-seekable file object
                 ``None`` (unknown)
        """
        if self._path is not None:
            return os.stat(self._path).st_size
        assert self._file is not None
        if not self._file.seekable():
            return None
        # measure the full size, leaving the current position untouched
        position = self._file.tell()
        size = self._file.seek(0, io.SEEK_END)
        self._file.seek(position)
        return size

    def chunks(self) -> Iterator[bytes]:
        """
        :return: the file contents as an iterator of chunks of (up to)
                 the configured chunk size
        """
        if self._path is not None:
            with open(self._path, "rb") as file:
                while chunk := file.read(self._chunk_size):
                    yield chunk
            return
        assert self._file is not None
        if self._file.seekable():
            self._file.seek(0)
        while chunk := self._file.read(self._chunk_size):
            yield chunk

    async def achunks(self) -> AsyncIterator[bytes]:
        """
        :return: the file contents as an asynchronous iterator of chunks;
                 all file operations run in a worker thread
        """
        if self._path is not None:
            opened = await _in_thread(_open_binary, self._path)
            try:
                while chunk := await _in_thread(opened.read, self._chunk_size):
                    yield chunk
            finally:
                # shielded: the handle is closed also when the consumer
                # was cancelled
                await _in_thread_shielded(opened.close)
            return
        file = self._file
        assert file is not None
        if await _in_thread(file.seekable):
            await _in_thread(file.seek, 0)
        while chunk := await _in_thread(file.read, self._chunk_size):
            yield chunk

    def as_bytes(self) -> bytes:
        """
        :return: the whole file contents
        """
        return b"".join(self.chunks())


class _OpenedFileBody(FileBody):
    """
    A :py:class:`FileBody` over a file it has opened itself, and which it
    therefore closes: what :py:meth:`Response.body_file()
    <action0.req.response.Response.body_file>` replaces a path-based
    ``FileBody`` with. The plain class cannot do that — it holds no file
    between two uses of a path, and never closes a file object it was
    given.
    """

    def __init__(self, path: Union[str, "os.PathLike[str]"], chunk_size: int) -> None:
        """
        :param path: the path of the file, which is opened right away
        :param chunk_size: the number of bytes per chunk
        """
        super().__init__(_open_binary(path), chunk_size)

    def close(self) -> None:
        """
        Close the file. Safe to call any number of times; the body cannot
        be read afterwards.
        """
        assert self._file is not None
        self._file.close()


def _file_of(body: FileBody) -> Union[tuple[FileBody, IO[bytes]], None]:
    """
    The seekable file object behind a :py:class:`FileBody`, so that
    nobody has to copy a file in order to get a file.

    :param body: the body to look into
    :return: the body to go on with and its file, rewound to the start —
             the given body if it wraps a seekable file object; for a
             path a new body that has opened the file and closes it in
             its ``close()``. ``None`` if the file is not seekable
    """
    if body._path is not None:
        body = _OpenedFileBody(body._path, body._chunk_size)
    file = body._file
    assert file is not None
    if not file.seekable():
        if isinstance(body, _OpenedFileBody):
            # a path leading to a pipe or the like: not kept open
            body.close()
        return None
    file.seek(0)
    return body, file


class IterableBody:
    """
    A :py:class:`BodyProducer` wrapping an iterable of byte chunks, e.g.
    a generator. The total length is unknown (:py:meth:`content_length`
    is ``None`` — such a body would be sent chunked).

    WARNING: if the iterable is a generator (or any other single-use
    iterable), the body can be consumed only once — also by
    :py:meth:`as_bytes`. Pass a list of chunks for a re-iterable body.

    A body streaming from a resource — e.g. an open connection — takes a
    ``close`` callback releasing it. The callback runs exactly once: when
    the body has been consumed, when a consumer gives up early, or on
    :py:meth:`close` — whichever comes first. That makes cleanup
    deterministic even for a body that is never read, where a generator's
    own ``finally`` block would not run at all::

        >>> body = IterableBody(iter([b"a", b"b"]), close=lambda: print("released"))
        >>> with body:
        ...     pass  # e.g. the status says the body is not worth reading
        released
        >>> body.close()  # nothing happens: the callback has run
    """

    def __init__(
        self,
        iterable: Iterable[bytes],
        *,
        close: Union[Callable[[], None], None] = None,
    ) -> None:
        """
        :param iterable: the byte chunks of the body
        :param close: called once to release the resource behind the
                      iterable, see :py:meth:`close`
        """
        self._iterable = iterable
        self._close = close

    def content_length(self) -> Union[int, None]:
        """
        :return: always ``None``, the length is unknown in advance
        """
        return None

    def chunks(self) -> Iterator[bytes]:
        """
        :return: the chunks as given by the wrapped iterable; the body is
                 closed when the iteration ends, for whatever reason
        """
        try:
            yield from self._iterable
        finally:
            self.close()

    async def achunks(self) -> AsyncIterator[bytes]:
        """
        :return: the chunks as given by the wrapped (synchronous)
                 iterable; the body is closed when the iteration ends,
                 for whatever reason
        """
        try:
            for chunk in self._iterable:
                yield chunk
        finally:
            self.close()

    def as_bytes(self) -> bytes:
        """
        :return: all chunks joined (consumes a single-use iterable); the
                 body is closed afterwards
        """
        try:
            return b"".join(self._iterable)
        finally:
            self.close()

    def close(self) -> None:
        """
        Release the resource behind the body by running the ``close``
        callback given to the constructor. Safe to call any number of
        times: the callback runs only once, and a body without a callback
        has nothing to release.
        """
        # swap before calling, so a failing or re-entrant callback cannot
        # run a second time
        close, self._close = self._close, None
        if close is not None:
            close()

    def __enter__(self) -> "IterableBody":
        """
        :return: the body itself; leaving the ``with`` block closes it
        """
        return self

    def __exit__(
        self,
        exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        traceback: Union[TracebackType, None],
    ) -> None:
        """
        Close the body, see :py:meth:`close`.
        """
        self.close()


class AsyncIterableBody:
    """
    A :py:class:`BodyProducer` wrapping an asynchronous iterable of byte
    chunks, e.g. an async generator proxying another stream. The total
    length is unknown (:py:meth:`content_length` is ``None``).

    An asynchronous source is async-only: the synchronous accessors
    :py:meth:`chunks` and :py:meth:`as_bytes` raise a
    :py:class:`RuntimeError` — consume the body with :py:meth:`achunks`.
    Like a generator, an async generator source is consumable only once.

    Like :py:class:`IterableBody` it takes a callback releasing the
    resource behind the stream — here an asynchronous one, ``aclose``. It
    runs exactly once: when the body has been consumed, when a consumer
    gives up early, or on :py:meth:`aclose`.
    """

    def __init__(
        self,
        aiterable: AsyncIterable[bytes],
        *,
        aclose: Union[Callable[[], Awaitable[None]], None] = None,
    ) -> None:
        """
        :param aiterable: the byte chunks of the body
        :param aclose: called (and awaited) once to release the resource
                       behind the iterable, see :py:meth:`aclose`
        """
        self._aiterable = aiterable
        self._aclose = aclose

    def content_length(self) -> Union[int, None]:
        """
        :return: always ``None``, the length is unknown in advance
        """
        return None

    def chunks(self) -> Iterator[bytes]:
        """
        :raises RuntimeError: always — an async body has no synchronous
                              chunks; use :py:meth:`achunks`
        """
        raise RuntimeError("an async body cannot be read synchronously; use achunks()")

    async def achunks(self) -> AsyncIterator[bytes]:
        """
        :return: the chunks as given by the wrapped asynchronous
                 iterable; the body is closed when the iteration ends,
                 for whatever reason
        """
        try:
            async for chunk in self._aiterable:
                yield chunk
        finally:
            await self.aclose()

    def as_bytes(self) -> bytes:
        """
        :raises RuntimeError: always — an async body has no synchronous
                              bytes view; use :py:meth:`achunks`
        """
        raise RuntimeError("an async body cannot be read synchronously; use achunks()")

    def close(self) -> None:
        """
        The synchronous counterpart of :py:meth:`aclose`, for code that
        closes bodies without knowing their kind: it does nothing if
        there is nothing (left) to release — no ``aclose`` callback, or
        one that has run already.

        :raises RuntimeError: if the ``aclose`` callback is still to run:
                              it can only be awaited — use
                              :py:meth:`aclose`
        """
        if self._aclose is not None:
            raise RuntimeError("an async body cannot be closed synchronously; use aclose()")

    async def aclose(self) -> None:
        """
        Release the resource behind the body by awaiting the ``aclose``
        callback given to the constructor. Safe to call any number of
        times: the callback runs only once, and a body without a callback
        has nothing to release.
        """
        # swap before calling, so a failing or re-entrant callback cannot
        # run a second time
        aclose, self._aclose = self._aclose, None
        if aclose is not None:
            await aclose()

    async def __aenter__(self) -> "AsyncIterableBody":
        """
        :return: the body itself; leaving the ``async with`` block closes
                 it
        """
        return self

    async def __aexit__(
        self,
        exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        traceback: Union[TracebackType, None],
    ) -> None:
        """
        Close the body, see :py:meth:`aclose`.
        """
        await self.aclose()


def _closes_async_only(body: object) -> bool:
    """
    :param body: the body of a response, in any form
    :return: whether the body holds something that only an awaited
             ``aclose()`` can release
    """
    if isinstance(body, AsyncIterableBody):
        # its close() exists, but refuses while the callback is pending
        return body._aclose is not None
    return callable(getattr(body, "aclose", None)) and not callable(getattr(body, "close", None))


class _Spool(tempfile.SpooledTemporaryFile[bytes]):
    """
    The file behind a :py:class:`SpooledBody`: a
    :py:class:`tempfile.SpooledTemporaryFile` that tells whether it has
    been rolled over to a real file.

    The standard library keeps that state private, and it does not only
    change when the body asks for it: ``fileno()`` and ``truncate(size)``
    — called by whoever was handed the file — roll over too. They all go
    through :py:meth:`rollover`, so this is the one place that knows.
    """

    #: whether the data has been moved from memory to a temporary file
    rolled = False

    def rollover(self) -> None:
        """
        Move the data from memory to a temporary file; does nothing if
        that has happened already.

        :raises ValueError: if the file is closed
        """
        if self.closed:
            # the standard library would create the temporary file first
            # and fail afterwards — leaving a closed spool reopened
            raise ValueError("I/O operation on closed file")
        super().rollover()
        self.rolled = True


class SpooledBody:
    """
    A :py:class:`BodyProducer` that is written first and read afterwards:
    the place to put a body while it arrives, so the connection can be
    released before anyone looks at the data. Like
    :py:class:`tempfile.SpooledTemporaryFile` it keeps the data in memory
    up to a threshold and moves it to an anonymous temporary file beyond
    that, so the memory use stays bounded however large the body is.

    Unlike the streaming producers it is re-readable any number of times,
    and :py:meth:`file` offers the data as a seekable binary file object
    — what streaming parsers and decompressors ask for::

        >>> body = SpooledBody(max_memory=4)
        >>> body.write(b"abc")
        3
        >>> body.rolled
        False
        >>> body.write(b"def")
        3
        >>> body.rolled
        True
        >>> body.content_length()
        6
        >>> body.file().read()
        b'abcdef'
        >>> body.file().read(3)  # rewound on every call
        b'abc'
        >>> body.close()
        >>> body.closed
        True

    The temporary file is removed by :py:meth:`close` — or by leaving a
    ``with`` block, or by closing the file object returned by
    :py:meth:`file`, which is the same file. Garbage collection removes
    it as a last resort only.

    Not safe for concurrent use: the write position, the file view and
    the chunk iterations all share one file object.
    """

    def __init__(
        self,
        *,
        max_memory: int = 1048576,
        dir: Union[str, "os.PathLike[str]", None] = None,
        chunk_size: int = 65536,
    ) -> None:
        """
        :param max_memory: how many bytes are kept in memory; the write
                           that would exceed this moves everything to a
                           temporary file. ``0`` writes to the file from
                           the first byte on
        :param dir: the directory for the temporary file, by default the
                    system's (see :py:func:`tempfile.gettempdir`)
        :param chunk_size: the number of bytes per chunk when reading the
                           body back via :py:meth:`chunks`/:py:meth:`achunks`
        :raises ValueError: if ``max_memory`` is negative or the chunk
                            size is not positive
        """
        if max_memory < 0:
            raise ValueError(f"max_memory must not be negative, got {max_memory}")
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")
        self._max_memory = max_memory
        self._chunk_size = chunk_size
        # max_size=0 turns off the automatic rollover: this class decides
        # when to move to disk, so that awrite() can do it in a thread.
        # The mode is "r+b" rather than the default "w+b" for the sake of
        # consumers that look at the ``mode`` attribute of a file they
        # are given: gzip.GzipFile(fileobj=...) would open for writing
        self._file = _Spool(max_size=0, mode="r+b", dir=None if dir is None else os.fspath(dir))
        self._size = 0

    @property
    def rolled(self) -> bool:
        """Whether the data has been moved from memory to the temporary
        file. Besides a write exceeding ``max_memory`` this happens when
        a consumer of :py:meth:`file` asks for a file descriptor
        (``fileno()``)."""
        return self._file.rolled

    @property
    def closed(self) -> bool:
        """Whether the body has been closed — and with it the data
        discarded."""
        return self._file.closed

    def write(self, data: bytes) -> int:
        """
        Append data to the body.

        :param data: the bytes to append
        :return: the number of bytes written (all of them)
        :raises ValueError: if the body is closed
        """
        file = self._file
        if file.closed:
            raise ValueError("I/O operation on closed file")
        if not file.rolled and self._size + len(data) > self._max_memory:
            file.rollover()
        # the readers (file(), chunks()) share this file object and leave
        # its position wherever they stopped: an append goes to the end.
        # Compared with tell() rather than always seeking, because a seek
        # would flush the write buffer of the temporary file every time
        if file.tell() != self._size:
            file.seek(self._size)
        file.write(data)
        self._size += len(data)
        return len(data)

    async def awrite(self, data: bytes) -> int:
        """
        Append data to the body without blocking the event loop.

        While the data fits in memory this is a plain in-memory append.
        The rollover to the temporary file and every write after it are
        file I/O and run in a worker thread — one thread hop per call, so
        pass chunks of a reasonable size (tens of kilobytes) for a large
        body.

        :param data: the bytes to append
        :return: the number of bytes written (all of them)
        :raises ValueError: if the body is closed
        """
        if not self._file.rolled and self._size + len(data) <= self._max_memory:
            return self.write(data)
        return await _in_thread(self.write, data)

    def file(self) -> IO[bytes]:
        """
        The body as a binary file object: seekable, readable any number
        of times, and rewound to the start by every call of this method.

        It is the body's own file, not a copy: closing it closes the body
        (which is the way to discard the data when only the file object
        is passed on), and reading it moves the position that
        :py:meth:`chunks` and friends start from anew.

        :return: the file object, positioned at the start
        :raises ValueError: if the body is closed
        """
        self._file.seek(0)
        return self._file

    def content_length(self) -> Union[int, None]:
        """
        :return: the number of bytes written so far
        """
        return self._size

    def chunks(self) -> Iterator[bytes]:
        """
        :return: the body from the start as an iterator of chunks of (up
                 to) the configured chunk size — re-iterable
        """
        file = self.file()
        while chunk := file.read(self._chunk_size):
            yield chunk

    async def achunks(self) -> AsyncIterator[bytes]:
        """
        :return: the body from the start as an asynchronous iterator of
                 chunks; once the data is in the temporary file, all file
                 operations run in a worker thread
        """
        if not self._file.rolled:
            # still in memory: nothing here can block
            for chunk in self.chunks():
                yield chunk
            return
        file = await _in_thread(self.file)
        while chunk := await _in_thread(file.read, self._chunk_size):
            yield chunk

    def as_bytes(self) -> bytes:
        """
        :return: the whole body — for a body spooled because of its size
                 this loads all of it into memory
        """
        return self.file().read()

    def close(self) -> None:
        """
        Discard the data: free the memory or remove the temporary file.
        Safe to call any number of times; every other access raises a
        :py:class:`ValueError` afterwards.
        """
        self._file.close()

    async def aclose(self) -> None:
        """
        Like :py:meth:`close`, without blocking the event loop: removing
        a temporary file is file I/O and runs in a worker thread — also
        when the calling task has been cancelled.
        """
        if self._file.rolled and not self._file.closed:
            await _in_thread_shielded(self._file.close)
        else:
            self._file.close()

    def __enter__(self) -> "SpooledBody":
        """
        :return: the body itself; leaving the ``with`` block closes it
        """
        return self

    def __exit__(
        self,
        exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        traceback: Union[TracebackType, None],
    ) -> None:
        """
        Close the body, see :py:meth:`close`.
        """
        self.close()

    async def __aenter__(self) -> "SpooledBody":
        """
        :return: the body itself; leaving the ``async with`` block closes
                 it
        """
        return self

    async def __aexit__(
        self,
        exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        traceback: Union[TracebackType, None],
    ) -> None:
        """
        Close the body, see :py:meth:`aclose`.
        """
        await self.aclose()


def save_body(
    body: Union[bytes, BodyProducer],
    target: Union[str, "os.PathLike[str]", IO[bytes]],
    *,
    append: bool = False,
) -> int:
    """
    Copy a body into a file, chunk by chunk, so that a streamed body
    never has to fit in memory.

    Example::

        >>> import io
        >>> buffer = io.BytesIO()
        >>> save_body(IterableBody([b"chunk1", b"chunk2"]), buffer)
        12
        >>> buffer.getvalue()
        b'chunk1chunk2'

    If the body breaks off midway the exception passes through, and
    everything received up to then has been written — which is what
    resuming a download relies on: the size of the file is the offset to
    continue from.

    A path is opened only once the body has delivered its first chunk: a
    body that cannot be read at all (closed, async-only, failing right
    away) raises without creating or truncating the file.

    :param body: the body to copy, as bytes or as a producer
    :param target: a path — the file is created or truncated, or appended
                   to with ``append=True``, and closed afterwards — or a
                   binary file object, which is written to at its current
                   position and left open
    :param append: for a path: keep the existing contents of the file and
                   write behind them. Has no effect for a file object
    :return: the number of bytes written
    """
    chunks: Iterator[bytes] = iter((body,) if isinstance(body, bytes) else body.chunks())
    if not isinstance(target, (str, os.PathLike)):
        return _write_chunks(target, chunks)
    # before the file is touched: what is in it must survive a body that
    # turns out to be unreadable
    first = next(chunks, b"")
    with _open_binary(target, "ab" if append else "wb") as file:
        return _write_chunks(file, itertools.chain((first,), chunks))


async def asave_body(
    body: Union[bytes, BodyProducer],
    target: Union[str, "os.PathLike[str]", IO[bytes]],
    *,
    append: bool = False,
) -> int:
    """
    :py:func:`save_body` for asynchronous code: the body is read via
    ``achunks()`` and every file operation (opening, writing, closing)
    runs in a worker thread, so the event loop is never stalled. Small
    chunks are collected and written 64 KiB at a time, which bounds the
    number of thread hops.

    When the task is cancelled midway (a timeout), what has arrived is
    still written and the file closed before the cancellation is passed
    on.

    :param body: the body to copy, as bytes or as a producer
    :param target: a path or a binary file object, as for
                   :py:func:`save_body`
    :param append: for a path: keep the existing contents of the file and
                   write behind them. Has no effect for a file object
    :return: the number of bytes written
    """
    chunks = _achunks(body)
    if not isinstance(target, (str, os.PathLike)):
        return await _awrite_chunks(target, chunks)
    # before the file is touched, as in save_body()
    first = await anext(chunks, b"")
    file = await _in_thread(_open_binary, target, "ab" if append else "wb")
    try:
        return await _awrite_chunks(file, chunks, first)
    finally:
        await _in_thread_shielded(file.close)


def _achunks(body: Union[bytes, BodyProducer]) -> AsyncIterator[bytes]:
    """
    :param body: a body, as bytes or as a producer
    :return: its chunks as an asynchronous iterator; bytes are a single
             chunk
    """
    if isinstance(body, bytes):
        return _one_chunk(body)
    return aiter(body.achunks())


async def _one_chunk(data: bytes) -> AsyncIterator[bytes]:
    """
    :param data: the bytes of a body
    :return: an asynchronous iterator producing them as a single chunk
    """
    yield data


def _write_all(file: IO[bytes], data: bytes) -> None:
    """
    Write all of the data to a file object. A buffered file takes
    everything in one call; a raw (unbuffered) file, pipe or socket may
    take only a part, and says so with the number it returns — the rest
    is then written in further calls.

    :param file: the binary file object to write to
    :param data: the bytes to write
    :raises OSError: if the file takes no data at all
    """
    # None is not in the signature of IO.write(), but it is what many a
    # file-like object returns: taken as "everything written"
    written = cast("Union[int, None]", file.write(data))
    while written is not None and written < len(data):
        if written <= 0:
            raise OSError("the file did not accept any data")
        data = data[written:]
        written = cast("Union[int, None]", file.write(data))


def _write_chunks(file: IO[bytes], chunks: Iterable[bytes]) -> int:
    """
    Write the chunks of a body to an open file object.

    :param file: the binary file object to write to
    :param chunks: the chunks of the body
    :return: the number of bytes written
    """
    written = 0
    for chunk in chunks:
        _write_all(file, chunk)
        written += len(chunk)
    return written


async def _awrite_chunks(file: IO[bytes], chunks: AsyncIterator[bytes], first: bytes = b"") -> int:
    """
    Write the chunks of a body to an open file object, doing the writes
    in a worker thread and batching small chunks.

    :param file: the binary file object to write to
    :param chunks: the chunks of the body
    :param first: a chunk taken off the body beforehand, written ahead of
                  the others
    :return: the number of bytes written
    """
    written = 0
    pending: list[bytes] = [first] if first else []
    pending_size = len(first)

    async def flush(*, shielded: bool = False) -> None:
        nonlocal written, pending, pending_size
        # hand the batch over before writing: if the write fails, the
        # ``finally`` below must not write the same bytes a second time
        batch, size = pending, pending_size
        pending, pending_size = [], 0
        if shielded:
            await _in_thread_shielded(_write_chunks, file, batch)
        else:
            await _in_thread(_write_chunks, file, batch)
        written += size

    try:
        async for chunk in chunks:
            pending.append(chunk)
            pending_size += len(chunk)
            if pending_size >= _WRITE_BATCH:
                await flush()
    finally:
        # also when the body broke off: what has arrived belongs in the
        # file, a resumed download continues behind it. Shielded, because
        # "broke off" includes this task being cancelled
        if pending:
            await flush(shielded=True)
    return written


BodyTypes = Union[bytes, str, BodyProducer]
"""Everything a request or response accepts as body: raw bytes, text
(encoded with the Content-Type charset when accessed as bytes), or a
streaming :py:class:`BodyProducer`."""


# the conversion helpers shared by Request and Response — each class keeps
# its own thin, documented body_bytes()/body_str()/body_producer() methods


def _charset(headers: Headers) -> str:
    """
    Extract the charset parameter of the Content-Type header.

    :param headers: the headers of the request or response
    :return: the charset, ``"utf-8"`` if there is none
    """
    content_type = headers.get(Header.CONTENT_TYPE, "")
    for parameter in content_type.split(";")[1:]:
        name, sep, value = parameter.partition("=")
        if sep and name.strip().lower() == "charset":
            return value.strip().strip('"') or "utf-8"
    return "utf-8"


def _body_bytes(body: Union[BodyTypes, None], charset: str) -> Union[bytes, None]:
    """
    Convert a body of any accepted form to bytes.

    :param body: the body as set
    :param charset: the charset for encoding a str body
    :return: the body bytes, ``None`` if there is no body
    """
    if body is None:
        return None
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode(charset)
    return body.as_bytes()


def _body_str(body: Union[BodyTypes, None], charset: str) -> Union[str, None]:
    """
    Convert a body of any accepted form to text.

    :param body: the body as set
    :param charset: the charset for decoding a bytes (or produced) body
    :return: the body text, ``None`` if there is no body
    """
    if body is None:
        return None
    if isinstance(body, str):
        return body
    if isinstance(body, bytes):
        return body.decode(charset)
    return body.as_bytes().decode(charset)


def _body_producer(body: Union[BodyTypes, None], charset: str) -> Union[BodyProducer, None]:
    """
    Convert a body of any accepted form to a streaming producer.

    :param body: the body as set
    :param charset: the charset for encoding a str body
    :return: the body producer, ``None`` if there is no body
    """
    if body is None:
        return None
    if isinstance(body, bytes):
        return BytesBody(body)
    if isinstance(body, str):
        return BytesBody(body.encode(charset))
    return body


def _rendered_body(body: BodyTypes, charset: str) -> str:
    """
    The body as text for a debug rendering: bytes/str decoded, a producer
    NOT consumed (it may be consumable only once) but shown as a
    placeholder.

    :param body: the body as set
    :param charset: the charset for decoding a bytes body
    :return: the body text or a ``"<ClassName>"`` placeholder
    """
    if isinstance(body, (bytes, str)):
        return body if isinstance(body, str) else body.decode(charset)
    return f"<{type(body).__name__}>"

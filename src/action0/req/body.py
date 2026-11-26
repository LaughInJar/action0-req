"""Streaming abstractions for request and response bodies."""

import asyncio
import errno
import functools
import inspect
import io
import itertools
import os
import stat
import sys
import tempfile
import threading
from types import TracebackType
from typing import IO
from typing import Any
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

    A thread cannot be stopped, so a function that has started is not
    abandoned: if the calling task is cancelled in the meantime, the
    function still runs to its end before the cancellation is passed on.
    Nothing else gets to touch the file it works on while it does. On
    asyncio the result of the function is lost in that case — a function
    whose result must not get lost (an opened file) records it itself.

    That is for calls that are over soon: writes, and whatever happens to
    the library's own temporary files. A call that may block for as long
    as somebody else likes goes through :py:func:`_in_thread_abandonable`.

    trio does not start the function at all in a scope that is cancelled
    already; :py:func:`_in_thread_shielded` is for what has to run
    regardless.

    :param function: the blocking function
    :param args: its positional arguments
    :param kwargs: its keyword arguments
    :return: what the function returned
    :raises RuntimeError: if called outside of asyncio and trio
    """
    return await _dispatch(functools.partial(function, *args, **kwargs))


async def _in_thread_shielded(
    function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs
) -> _T:
    """
    :py:func:`_in_thread` for cleanup that must not be skipped — closing
    a file, writing out what has arrived: the function runs also when the
    calling task has been cancelled before (a timeout, a failing sibling
    task). The cancellation is not swallowed, only postponed: it is
    raised once the function has returned.

    A cleanup typically runs *because* of a cancellation, and trio
    refuses to start a worker thread in a cancelled scope.

    :param function: the blocking function
    :param args: its positional arguments
    :param kwargs: its keyword arguments
    :return: what the function returned
    :raises RuntimeError: if called outside of asyncio and trio
    """
    return await _dispatch(functools.partial(function, *args, **kwargs), shield=True)


async def _in_thread_abandonable(
    function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs
) -> _T:
    """
    :py:func:`_in_thread` for a call that may block for as long as
    somebody else likes — opening or reading what is really a pipe or a
    device: a cancellation of the calling task is passed on at once and
    the thread is left behind, to finish whenever the call returns.
    Waiting for it would make a stalled read impossible to time out.

    On asyncio the call gets a daemon thread of its own instead of one
    from the loop's thread pool: a thread stuck there would be waited for
    when the loop shuts down, and keep a place in the pool occupied.

    The caller has to cope with the function still running afterwards.

    :param function: the blocking function
    :param args: its positional arguments
    :param kwargs: its keyword arguments
    :return: what the function returned
    :raises RuntimeError: if called outside of asyncio and trio
    """
    return await _dispatch(functools.partial(function, *args, **kwargs), abandon=True)


async def _dispatch(call: Callable[[], _T], *, shield: bool = False, abandon: bool = False) -> _T:
    """
    The worker-thread dispatch behind :py:func:`_in_thread` and its two
    variants.

    :param call: the blocking function, with its arguments bound
    :param shield: whether to run it in a scope that is cancelled already
                   (makes a difference on trio only: asyncio cannot tell
                   beforehand, and always starts the function)
    :param abandon: whether a cancellation leaves the thread behind
                    instead of waiting for it
    :return: what the function returned
    :raises RuntimeError: if called outside of asyncio and trio
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # looked up instead of imported: trio is not a dependency, and
        # code running below trio has necessarily imported it already
        trio = sys.modules.get("trio")
        if trio is None:
            raise
        run_sync = trio.to_thread.run_sync
        if abandon:
            # the keyword got its name with trio 0.23
            parameters = inspect.signature(run_sync).parameters
            keyword = "abandon_on_cancel" if "abandon_on_cancel" in parameters else "cancellable"
            return cast("_T", await run_sync(call, **{keyword: True}))
        # trio waits for a thread that has started by itself, and
        # delivers a cancellation at the next checkpoint after it
        if not shield:
            return cast("_T", await run_sync(call))
        with trio.CancelScope(shield=True):
            return cast("_T", await run_sync(call))
    if abandon:
        return await _in_daemon_thread(loop, call)
    future = asyncio.ensure_future(asyncio.to_thread(call))
    cancelled: Union[asyncio.CancelledError, None] = None
    while not future.done():
        try:
            # shield() keeps the cancellation of this task away from the
            # future; waiting again — as often as the task is cancelled,
            # anyio does it repeatedly — lets the function finish first
            await asyncio.shield(future)
        except asyncio.CancelledError as error:
            cancelled = error
        except Exception:
            # the function's own error: raised by result() below
            pass
    if cancelled is not None:
        raise cancelled
    return future.result()


async def _in_daemon_thread(loop: asyncio.AbstractEventLoop, call: Callable[[], _T]) -> _T:
    """
    Run a blocking function in a daemon thread of its own and wait for
    its result — unless the waiting task is cancelled: then the thread is
    simply left behind. Being a daemon, it holds up neither the shutdown
    of the loop nor the exit of the interpreter.

    :param loop: the running event loop
    :param call: the blocking function, with its arguments bound
    :return: what the function returned
    """
    future: asyncio.Future[_T] = loop.create_future()

    def deliver(result: Union[_T, None], error: Union[BaseException, None]) -> None:
        if future.done():
            # cancelled: nobody is waiting anymore
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(cast("_T", result))

    def run() -> None:
        try:
            outcome: tuple[Union[_T, None], Union[BaseException, None]] = (call(), None)
        except BaseException as error:
            outcome = (None, error)
        try:
            loop.call_soon_threadsafe(deliver, *outcome)
        except RuntimeError:
            # the loop has been closed in the meantime
            pass

    threading.Thread(target=run, name="action0.req blocking call", daemon=True).start()
    return await future


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


class _OwnedFile:
    """
    The file :py:meth:`FileBody.achunks` opens for a path, used from
    worker threads.

    If the path is not a regular file — a named pipe, a device —, opening
    and reading it may block for as long as the other side likes. A
    cancelled consumer does not wait for that: the thread is left behind.
    This class is how the file gets closed nevertheless: by that thread,
    once the call it was left alone with returns.
    """

    def __init__(self, path: Union[str, "os.PathLike[str]"]) -> None:
        """
        :param path: the path of the file, opened by :py:meth:`open`
        """
        self._path = path
        self._file: Union[IO[bytes], None] = None
        self._lock = threading.Lock()
        # whether a thread is in open() or read() right now
        self._busy = False
        self._released = False

    def may_stall(self) -> bool:
        """
        :return: whether opening and reading the path may block
                 indefinitely: true for everything but a regular file
        """
        return not stat.S_ISREG(os.stat(self._path).st_mode)

    def open(self) -> None:
        """
        Open the file. The file object is kept here instead of returned:
        a result handed back from a worker thread gets lost if the task
        waiting for it is cancelled, and nobody could close the file.
        """
        with self._lock:
            if self._released:
                return
            self._busy = True
        file: Union[IO[bytes], None] = None
        try:
            file = _open_binary(self._path)
        finally:
            with self._lock:
                self._busy = False
                self._file = file
                if self._released and file is not None:
                    # left behind by a cancelled consumer: nobody else
                    # closes the file anymore
                    file.close()

    def read(self, size: int) -> bytes:
        """
        :param size: the number of bytes to read at most
        :return: the bytes read, empty at the end of the file — and when
                 the consumer has gone already
        """
        with self._lock:
            file = self._file
            if self._released or file is None:
                return b""
            self._busy = True
        try:
            return file.read(size)
        finally:
            with self._lock:
                self._busy = False
                if self._released:
                    # as in open()
                    file.close()

    def release(self) -> bool:
        """
        Called when the consumer is done with the file, for whatever
        reason.

        :return: whether the caller has to :py:meth:`close` the file now.
                 Not if it was never opened, and not while a thread is
                 still opening or reading it — that thread closes it
        """
        with self._lock:
            self._released = True
            return self._file is not None and not self._busy

    def close(self) -> None:
        """
        Close the file.
        """
        assert self._file is not None
        self._file.close()


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

    A source that may block for good — the path of a named pipe or a
    device, a file object that is not seekable — does not stand in the
    way of a timeout: when the consumer is cancelled, the cancellation is
    passed on at once and the call under way is left to finish on its
    own. With a regular file, or a seekable file object, the call under
    way is finished first, so the file is not touched behind the back of
    whoever reads the body next.
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
        # the files opened for Response.body_file(), see _file_view()
        self._views: list[IO[bytes]] = []

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
            owned = _OwnedFile(self._path)
            try:
                # only what may block for good is left behind on
                # cancellation, see the class docstring
                run = _in_thread_abandonable if await _in_thread(owned.may_stall) else _in_thread
                await run(owned.open)
                while chunk := await run(owned.read, self._chunk_size):
                    yield chunk
            finally:
                # shielded: the file is closed also when the consumer was
                # cancelled — unless a thread left behind is still at
                # work on it, which then closes the file itself
                if owned.release():
                    await _in_thread_shielded(owned.close)
            return
        file = self._file
        assert file is not None
        if await _in_thread(file.seekable):
            await _in_thread(file.seek, 0)
            run = _in_thread
        else:
            # a pipe, a socket: single use anyway, and free to stall
            run = _in_thread_abandonable
        while chunk := await run(file.read, self._chunk_size):
            yield chunk

    def as_bytes(self) -> bytes:
        """
        :return: the whole file contents
        """
        return b"".join(self.chunks())

    def _file_view(self) -> Union[IO[bytes], None]:
        """
        The seekable file object behind the body, for
        :py:meth:`Response.body_file()
        <action0.req.response.Response.body_file>` — so that nobody has
        to copy a file in order to get a file.

        A seekable file object given to the constructor is that file. For
        a path every call opens the file anew: the caller gets a file of
        its own, with its own position, to close whenever it likes —
        :py:meth:`close` closes those still open.

        :return: the file, positioned at the start. ``None`` if there is
                 no seekable file with the contents of the body: a
                 non-seekable file object; a path that is not a regular
                 file (a pipe is not opened just to find out — opening it
                 is not without effect on the writer); a subclass that
                 reads the file in its own way, and so may deliver
                 something else than what is in it
        """
        cls = type(self)
        if (
            cls.chunks is not FileBody.chunks
            or cls.achunks is not FileBody.achunks
            or cls.as_bytes is not FileBody.as_bytes
        ):
            return None
        if self._path is None:
            file = self._file
            assert file is not None
            if not file.seekable():
                return None
            file.seek(0)
            return file
        if not stat.S_ISREG(os.stat(self._path).st_mode):
            return None
        view = _open_binary(self._path)
        # forget those closed by their users, remember this one
        self._views = [*(file for file in self._views if not file.closed), view]
        return view

    def close(self) -> None:
        """
        Close the files that :py:meth:`Response.body_file()
        <action0.req.response.Response.body_file>` has opened for a
        path-based body and that are still open. That is all this body
        ever holds: a file object given to the constructor is never
        closed, and between two iterations of a path no file is open.

        Safe to call any number of times, and the body stays readable.
        """
        views, self._views = self._views, []
        for view in views:
            view.close()


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
    own ``finally`` block would not run at all. (A generator that has
    been started is closed along with the body, which runs its
    ``finally`` blocks.) ::

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
        self._has_callback = close is not None

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
        Release the resource behind the body: a generator given as the
        iterable is closed — for one that has been started this runs its
        ``finally`` blocks — and the ``close`` callback given to the
        constructor is run. Safe to call any number of times: the
        callback runs only once.

        A body over a generator cannot be read anymore afterwards. One
        over a list or another re-iterable is not affected, apart from
        its callback.

        :raises RuntimeError: if the generator is running right now (it
                              is being read in another thread) and there
                              is no callback that could stop it: nothing
                              would be released
        """
        # swap before calling, so a failing or re-entrant callback cannot
        # run a second time
        close, self._close = self._close, None
        try:
            # generators only: closing one that is exhausted or abandoned
            # does what the garbage collector would do later. Any other
            # iterable with a close() may well be meant to live on. And
            # not one that is running right now (in another thread, or
            # calling this very method): that would raise, and it ends
            # by itself once the callback has released what it reads
            source = self._iterable
            if inspect.isgenerator(source):
                if not source.gi_running:
                    source.close()
                elif not self._has_callback:
                    raise RuntimeError(
                        "the body is being read right now and has no close callback: "
                        "closing it would release nothing"
                    )
        finally:
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
        self._has_callback = aclose is not None
        # whether an async generator behind the body has been started and
        # not closed yet: it may hold a resource in a ``finally`` block
        self._open = False

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
        self._open = inspect.isasyncgen(self._aiterable)
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
        there is nothing (left) to release — no ``aclose`` callback to
        run, and no asynchronous generator that was read in part.

        :raises RuntimeError: if there is: the callback, and a generator
                              that has been started, can only be closed
                              by awaiting — use :py:meth:`aclose`
        """
        if self._aclose is not None or self._open:
            raise RuntimeError("an async body cannot be closed synchronously; use aclose()")

    async def aclose(self) -> None:
        """
        Release the resource behind the body: an asynchronous generator
        given as the iterable is closed — for one that has been started
        this runs its ``finally`` blocks — and the ``aclose`` callback
        given to the constructor is awaited. Safe to call any number of
        times: the callback runs only once.

        A body over an asynchronous generator cannot be read anymore
        afterwards.

        :raises RuntimeError: if another task is waiting in the generator
                              right now and there is no callback that
                              could stop it: nothing would be released —
                              cancel that task instead
        """
        # swap before calling, so a failing or re-entrant callback cannot
        # run a second time
        aclose, self._aclose = self._aclose, None
        try:
            # generators only, and not one that is running right now —
            # another task is waiting in it for the next chunk —, as in
            # IterableBody.close()
            source: object = self._aiterable
            if inspect.isasyncgen(source):
                if not source.ag_running:
                    await source.aclose()
                    self._open = False
                elif not self._has_callback:
                    raise RuntimeError(
                        "the body is being read by another task and has no aclose "
                        "callback: closing it would release nothing; cancel the reader"
                    )
        finally:
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


class _Spool(tempfile.SpooledTemporaryFile[bytes]):
    """
    The file behind a :py:class:`SpooledBody`: a
    :py:class:`tempfile.SpooledTemporaryFile` that tells whether it has
    been rolled over to a real file — the standard library keeps that
    state private.
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


class _SpoolView(io.RawIOBase):
    """
    A read-only, seekable view of a :py:class:`SpooledBody` with a
    position of its own — the raw file behind what
    :py:meth:`SpooledBody.file` returns.

    It reads through the body instead of being the body's file: closing
    it closes nothing but the view, and what one view (or one iteration
    of the body) reads does not move anybody else's position.
    """

    #: looked at by consumers that are given a file, e.g. ``gzip``
    mode = "rb"

    def __init__(self, body: "SpooledBody") -> None:
        """
        :param body: the body to read from
        """
        super().__init__()
        self._body = body
        self._position = 0

    def readable(self) -> bool:
        """
        :return: always ``True``
        """
        return True

    def seekable(self) -> bool:
        """
        :return: always ``True``
        """
        return True

    def readinto(self, buffer: Any, /) -> int:
        """
        :param buffer: the writable buffer to read into
        :return: the number of bytes read, ``0`` at the end of the body
        :raises ValueError: if the view or the body is closed
        """
        self._checkClosed()
        target = memoryview(buffer).cast("B")
        data = self._body._read_at(self._position, len(target))
        target[: len(data)] = data
        self._position += len(data)
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        """
        :param offset: the position, relative to ``whence``
        :param whence: ``io.SEEK_SET`` (the start), ``io.SEEK_CUR`` (the
                       current position) or ``io.SEEK_END`` (the end of
                       what has been written to the body so far)
        :return: the new position
        :raises ValueError: for an unknown ``whence`` or a position
                            before the start
        """
        self._checkClosed()
        if whence == io.SEEK_CUR:
            offset += self._position
        elif whence == io.SEEK_END:
            offset += self._body._size
        elif whence != io.SEEK_SET:
            raise ValueError(f"invalid whence ({whence}, should be 0, 1 or 2)")
        if offset < 0:
            raise ValueError(f"negative seek position {offset}")
        self._position = offset
        return offset

    def tell(self) -> int:
        """
        :return: the current position
        """
        self._checkClosed()
        return self._position


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
        >>> body.file().read(3)  # every call starts at the beginning
        b'abc'
        >>> body.close()
        >>> body.closed
        True

    The temporary file is removed by :py:meth:`close` or by leaving a
    ``with`` block. Garbage collection removes it as a last resort only.

    Readers do not get in each other's way: every iteration of the body
    and every file object from :py:meth:`file` has a position of its own,
    so the body can be read by several consumers at once (two tasks
    serving the same cached response, say). The steps touching the one
    file behind the body are serialized by a lock.

    Read a body once it is complete: a reader ends where the data ends at
    that moment, and cannot tell a body that is still being written from
    one that is finished.
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
        # when to move to disk, so that awrite() can do it in a thread
        self._file = _Spool(max_size=0, dir=None if dir is None else os.fspath(dir))
        self._size = 0
        # one file object, one position: whoever uses it — the writer and
        # every reader, possibly in different worker threads — moves the
        # position and does its read or write as one step
        self._lock = threading.Lock()

    @property
    def rolled(self) -> bool:
        """Whether the data has been moved from memory to the temporary
        file."""
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
        with self._lock:
            file = self._file
            if file.closed:
                raise ValueError("I/O operation on closed file")
            if not file.rolled and self._size + len(data) > self._max_memory:
                file.rollover()
            # the readers leave the position wherever they stopped
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
        The body as a binary file object: read-only, seekable, and
        positioned at the start.

        Every call returns a new, independent view with a position of its
        own. It reads from the body, it is not the body's file: closing
        it (or handing it to something that does, like
        :py:class:`io.TextIOWrapper`) leaves the body as it is, and it
        has no file descriptor (``fileno()``) — :py:func:`save_body`
        puts the data into a real file. Once the body is closed, reading
        the view raises a :py:class:`ValueError`.

        :return: the file object, positioned at the start
        :raises ValueError: if the body is closed
        """
        if self._file.closed:
            raise ValueError("I/O operation on closed file")
        return io.BufferedReader(_SpoolView(self))

    def content_length(self) -> Union[int, None]:
        """
        :return: the number of bytes written so far
        """
        return self._size

    def _read_at(self, offset: int, size: int) -> bytes:
        """
        Read from a position of the caller's own.

        :param offset: where to read
        :param size: the number of bytes to read at most, ``-1`` for all
                     up to the end
        :return: the bytes read, empty at (or beyond) the end
        :raises ValueError: if the body is closed
        """
        with self._lock:
            file = self._file
            if file.tell() != offset:
                file.seek(offset)
            return file.read(size)

    def _read_inline(self, offset: int, size: int) -> Union[bytes, None]:
        """
        :py:meth:`_read_at` for the event loop: without waiting, and from
        memory only.

        :param offset: where to read
        :param size: the number of bytes to read at most
        :return: the bytes read — or ``None`` if reading here would
                 block: the data is in the temporary file, or a worker
                 thread holds the lock (a write that moves the data
                 there)
        :raises ValueError: if the body is closed
        """
        if not self._lock.acquire(blocking=False):
            return None
        try:
            file = self._file
            if file.rolled:
                return None
            if file.tell() != offset:
                file.seek(offset)
            return file.read(size)
        finally:
            self._lock.release()

    def chunks(self) -> Iterator[bytes]:
        """
        :return: the body from the start as an iterator of chunks of (up
                 to) the configured chunk size — re-iterable
        """
        offset = 0
        while chunk := self._read_at(offset, self._chunk_size):
            offset += len(chunk)
            yield chunk

    async def achunks(self) -> AsyncIterator[bytes]:
        """
        :return: the body from the start as an asynchronous iterator of
                 chunks; once the data is in the temporary file, the
                 reads run in a worker thread
        """
        offset = 0
        while True:
            # inline while the data is in memory and nothing can block
            chunk = self._read_inline(offset, self._chunk_size)
            if chunk is None:
                chunk = await _in_thread(self._read_at, offset, self._chunk_size)
            if not chunk:
                return
            offset += len(chunk)
            yield chunk

    def as_bytes(self) -> bytes:
        """
        :return: the whole body — for a body spooled because of its size
                 this loads all of it into memory
        """
        return self._read_at(0, -1)

    def close(self) -> None:
        """
        Discard the data: free the memory or remove the temporary file.
        Safe to call any number of times; every other access raises a
        :py:class:`ValueError` afterwards.
        """
        with self._lock:
            self._file.close()

    async def aclose(self) -> None:
        """
        Like :py:meth:`close`, without blocking the event loop: removing
        a temporary file is file I/O and runs in a worker thread — also
        when the calling task has been cancelled.
        """
        if self._file.rolled and not self._file.closed:
            await _in_thread_shielded(self.close)
        else:
            self.close()

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


class _SpooledStream(SpooledBody):
    """
    The spool that :py:meth:`Response.body_file()
    <action0.req.response.Response.body_file>` replaces a stream with
    whose resource only an awaited ``aclose()`` can release.

    ``body_file()`` is synchronous and cannot await it, and afterwards
    the response refers to the spool alone. So the spool takes the
    pending ``aclose()`` along: its own :py:meth:`aclose` awaits it, and
    its :py:meth:`close` refuses — like the stream would have.
    """

    _source_aclose: Union[Callable[[], Awaitable[None]], None] = None

    def release_with(self, aclose: Callable[[], Awaitable[None]]) -> None:
        """
        :param aclose: the ``aclose()`` of the stream this spool has
                       replaced, to be awaited by :py:meth:`aclose`
        """
        self._source_aclose = aclose

    def close(self) -> None:
        """
        Discard the data, see :py:meth:`SpooledBody.close`.

        :raises RuntimeError: if the stream behind the spool is still to
                              be closed, which only :py:meth:`aclose` can
                              do
        """
        if self._source_aclose is not None:
            raise RuntimeError("an async body cannot be closed synchronously; use aclose()")
        super().close()

    async def aclose(self) -> None:
        """
        Close the stream behind the spool (once), then discard the data,
        see :py:meth:`SpooledBody.aclose`.
        """
        aclose, self._source_aclose = self._source_aclose, None
        try:
            if aclose is not None:
                await aclose()
        finally:
            await super().aclose()


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
    continue from. If it is the write that fails, the stream is not left
    half-read: it is closed, which releases what it holds.

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
    :raises BlockingIOError: if the target is a raw file in non-blocking
                             mode that takes no more data
    """
    chunks: Iterator[bytes] = iter((body,) if isinstance(body, bytes) else body.chunks())
    try:
        if not isinstance(target, (str, os.PathLike)):
            return _write_chunks(target, chunks)
        # before the file is touched: what is in it must survive a body
        # that turns out to be unreadable
        first = next(chunks, b"")
        with _open_binary(target, "ab" if append else "wb") as file:
            return _write_chunks(file, itertools.chain((first,), chunks))
    finally:
        # matters when the copy failed midway: a generator suspended
        # half-read would hold its resource until garbage collection
        if inspect.isgenerator(chunks):
            chunks.close()


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

    A cancellation (a timeout) does not tear anything apart: a write
    that is under way is finished, and once the file is open, what has
    arrived is written and the file closed before the cancellation is
    passed on.

    :param body: the body to copy, as bytes or as a producer
    :param target: a path or a binary file object, as for
                   :py:func:`save_body`
    :param append: for a path: keep the existing contents of the file and
                   write behind them. Has no effect for a file object
    :return: the number of bytes written
    :raises BlockingIOError: if the target is a raw file in non-blocking
                             mode that takes no more data
    """
    chunks = _achunks(body)
    opened: list[IO[bytes]] = []
    try:
        if not isinstance(target, (str, os.PathLike)):
            return await _awrite_chunks(target, chunks)
        # before the file is touched, as in save_body()
        first = await anext(chunks, b"")
        # opening and the first write are one call, which records the
        # file itself: neither the handle nor the chunk already taken off
        # the body gets lost if this task is cancelled meanwhile
        mode = "ab" if append else "wb"
        await _in_thread(_open_and_write, opened, target, mode, first)
        return len(first) + await _awrite_chunks(opened[0], chunks)
    finally:
        try:
            if opened:
                await _in_thread_shielded(opened[0].close)
        finally:
            # as in save_body(): no half-read stream is left behind
            stream: object = chunks
            if inspect.isasyncgen(stream):
                await stream.aclose()


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


def _open_and_write(
    opened: list[IO[bytes]], path: Union[str, "os.PathLike[str]"], mode: str, data: bytes
) -> None:
    """
    Open a file, record it and write the first data to it. The file
    object is recorded instead of returned: a result handed back from a
    worker thread gets lost if the task waiting for it is cancelled, and
    nobody could close the file.

    :param opened: the list the file object is appended to
    :param path: the path of the file to open
    :param mode: the (binary) mode to open the file in
    :param data: the bytes to write
    """
    opened.append(_open_binary(path, mode))
    _write_all(opened[0], data)


def _write_all(file: IO[bytes], data: bytes) -> None:
    """
    Write all of the data to a file object. A buffered file takes
    everything in one call; a raw (unbuffered) file, pipe or socket may
    take only a part, and says so with the number it returns — the rest
    is then written in further calls.

    :param file: the binary file object to write to
    :param data: the bytes to write
    :raises BlockingIOError: if a raw file in non-blocking mode takes no
                             more data
    :raises OSError: if the file takes no data at all
    """
    remaining = data
    while True:
        written = cast("Union[int, None]", file.write(remaining))
        if written is None:
            if isinstance(file, io.RawIOBase):
                # how a raw file in non-blocking mode says that nothing
                # was written. Waiting for it is not this function's job
                raise BlockingIOError(
                    errno.EAGAIN,
                    "the file is in non-blocking mode and takes no more data",
                    len(data) - len(remaining),
                )
            # not in the signature of IO.write(), but what many a
            # hand-written file-like object returns: everything written
            return
        if written >= len(remaining):
            return
        if written <= 0:
            raise OSError("the file did not accept any data")
        remaining = remaining[written:]


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


async def _awrite_chunks(file: IO[bytes], chunks: AsyncIterator[bytes]) -> int:
    """
    Write the chunks of a body to an open file object, doing the writes
    in a worker thread and batching small chunks.

    :param file: the binary file object to write to
    :param chunks: the chunks of the body
    :return: the number of bytes written
    """
    written = 0
    pending: list[bytes] = []
    pending_size = 0

    def write_pending() -> None:
        nonlocal written, pending, pending_size
        # the batch is taken over before it is written: if the write
        # fails, the final flush must not write the same bytes again.
        # And it is taken over here, in the thread: a flush that trio
        # does not start (the scope is cancelled) leaves the batch where
        # the final flush finds it
        batch, size = pending, pending_size
        pending, pending_size = [], 0
        _write_chunks(file, batch)
        written += size

    try:
        async for chunk in chunks:
            pending.append(chunk)
            pending_size += len(chunk)
            if pending_size >= _WRITE_BATCH:
                await _in_thread(write_pending)
    finally:
        # also when the body broke off: what has arrived belongs in the
        # file, a resumed download continues behind it. Shielded, because
        # "broke off" includes this task being cancelled
        if pending:
            await _in_thread_shielded(write_pending)
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

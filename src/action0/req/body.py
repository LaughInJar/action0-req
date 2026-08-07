"""Streaming abstractions for request and response bodies."""

import asyncio
import io
import os
from typing import IO
from typing import AsyncIterable
from typing import AsyncIterator
from typing import Iterable
from typing import Iterator
from typing import Protocol
from typing import Union
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
    implementation; file- and iterable-backed producers are planned.

    The protocol is ``runtime_checkable``, so ``isinstance(obj, BodyProducer)``
    checks that the four methods exist.
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


def _open_binary(path: Union[str, "os.PathLike[str]"]) -> IO[bytes]:
    """
    Open a file for binary reading.

    A typed wrapper: passing the built-in ``open`` through
    :py:func:`asyncio.to_thread` loses the overload selection and with it
    the precise ``IO[bytes]`` return type.

    :param path: the path of the file to open
    :return: the opened binary file object
    """
    return open(path, "rb")


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

    :py:meth:`achunks` performs every blocking file operation in the
    default thread pool via :py:func:`asyncio.to_thread`, so the event
    loop is never stalled — without any extra dependency.
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
                 all file operations run in the default thread pool
        """
        if self._path is not None:
            opened = await asyncio.to_thread(_open_binary, self._path)
            try:
                while chunk := await asyncio.to_thread(opened.read, self._chunk_size):
                    yield chunk
            finally:
                await asyncio.to_thread(opened.close)
            return
        file = self._file
        assert file is not None
        if await asyncio.to_thread(file.seekable):
            await asyncio.to_thread(file.seek, 0)
        while chunk := await asyncio.to_thread(file.read, self._chunk_size):
            yield chunk

    def as_bytes(self) -> bytes:
        """
        :return: the whole file contents
        """
        return b"".join(self.chunks())


class IterableBody:
    """
    A :py:class:`BodyProducer` wrapping an iterable of byte chunks, e.g.
    a generator. The total length is unknown (:py:meth:`content_length`
    is ``None`` — such a body would be sent chunked).

    WARNING: if the iterable is a generator (or any other single-use
    iterable), the body can be consumed only once — also by
    :py:meth:`as_bytes`. Pass a list of chunks for a re-iterable body.
    """

    def __init__(self, iterable: Iterable[bytes]) -> None:
        """
        :param iterable: the byte chunks of the body
        """
        self._iterable = iterable

    def content_length(self) -> Union[int, None]:
        """
        :return: always ``None``, the length is unknown in advance
        """
        return None

    def chunks(self) -> Iterator[bytes]:
        """
        :return: the chunks as given by the wrapped iterable
        """
        yield from self._iterable

    async def achunks(self) -> AsyncIterator[bytes]:
        """
        :return: the chunks as given by the wrapped (synchronous) iterable
        """
        for chunk in self._iterable:
            yield chunk

    def as_bytes(self) -> bytes:
        """
        :return: all chunks joined (consumes a single-use iterable)
        """
        return b"".join(self._iterable)


class AsyncIterableBody:
    """
    A :py:class:`BodyProducer` wrapping an asynchronous iterable of byte
    chunks, e.g. an async generator proxying another stream. The total
    length is unknown (:py:meth:`content_length` is ``None``).

    An asynchronous source is async-only: the synchronous accessors
    :py:meth:`chunks` and :py:meth:`as_bytes` raise a
    :py:class:`RuntimeError` — consume the body with :py:meth:`achunks`.
    Like a generator, an async generator source is consumable only once.
    """

    def __init__(self, aiterable: AsyncIterable[bytes]) -> None:
        """
        :param aiterable: the byte chunks of the body
        """
        self._aiterable = aiterable

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
        :return: the chunks as given by the wrapped asynchronous iterable
        """
        async for chunk in self._aiterable:
            yield chunk

    def as_bytes(self) -> bytes:
        """
        :raises RuntimeError: always — an async body has no synchronous
                              bytes view; use :py:meth:`achunks`
        """
        raise RuntimeError("an async body cannot be read synchronously; use achunks()")


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

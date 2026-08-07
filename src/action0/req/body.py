"""Streaming abstractions for request and response bodies."""

from typing import AsyncIterator
from typing import Iterator
from typing import Protocol
from typing import Union
from typing import runtime_checkable


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

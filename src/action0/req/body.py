"""Streaming abstractions for request and response bodies."""

from typing import AsyncIterator
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

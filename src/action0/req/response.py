"""The HTTP response representation (:py:class:`Response`)."""

import io
import os
from types import TracebackType
from typing import IO
from typing import Any
from typing import Mapping
from typing import Union

from .body import BodyProducer
from .body import BodyTypes
from .body import BytesBody
from .body import FileBody
from .body import SpooledBody
from .body import _body_bytes
from .body import _body_producer
from .body import _body_str
from .body import _charset
from .body import _closes_async_only
from .body import _file_of
from .body import _rendered_body
from .body import asave_body
from .body import save_body
from .headers import Headers
from .headers import HeaderTypes
from .request import Request
from .status import Status


class Response:
    r"""
    Python representation of an HTTP response: status, headers, body and
    HTTP version — every part a plain mutable attribute (the headers a
    :py:class:`~action0.req.headers.Headers`).

    Example::

        >>> resp = Response(404, headers={"Content-Type": "text/plain"}, body="not here")
        >>> resp.status
        404
        >>> resp.phrase
        'Not Found'
        >>> resp.is_client_error
        True
        >>> print(resp.as_str(include_body=True, separator="\n"))
        HTTP/1.1 404 Not Found
        Content-Type: text/plain
        <BLANKLINE>
        not here
        >>> resp
        Response(404 Not Found)

    The body works exactly like the request body: set as ``bytes``,
    ``str`` or a streaming :py:class:`~action0.req.body.BodyProducer`,
    retrieved in any of the three forms via :py:meth:`body_bytes`,
    :py:meth:`body_str` and :py:meth:`body_producer`. On top of that a
    response body can be read as a file (:py:meth:`body_file`) and copied
    into one (:py:meth:`save_body`, :py:meth:`asave_body`).

    A response whose body holds a resource — a streamed body its
    connection, a spooled body its temporary file — is released with
    :py:meth:`close` or :py:meth:`aclose`, or by using the response as a
    (sync or async) context manager. Both are harmless no-ops for a plain
    bytes or str body::

        >>> with Response(200, body=b"data") as resp:
        ...     resp.body_bytes()
        b'data'
    """

    def __init__(
        self,
        status: int = Status.OK,
        *,
        headers: Union[HeaderTypes, None] = None,
        body: Union[BodyTypes, None] = None,
        reason: Union[str, None] = None,
        http_version: str = "HTTP/1.1",
        request: Union[Request, None] = None,
        meta: Union[Mapping[str, Any], None] = None,
    ) -> None:
        """
        :param status: the status code, a :py:class:`~action0.req.status.Status`
                       member or any int — unregistered codes are fine
        :param headers: the initial header lines, in any form accepted by
                        :py:class:`~action0.req.headers.Headers`
        :param body: the response body as ``bytes``, ``str`` or a streaming
                     :py:class:`~action0.req.body.BodyProducer`
        :param reason: the reason phrase as sent by the server; ``None``
                       falls back to the registry phrase of the status code
                       (see :py:attr:`phrase`)
        :param http_version: the protocol version rendered in the status
                             line
        :param request: the request that produced this response — metadata:
                        shared, not copied, and not part of equality
        :param meta: initial application metadata riding along with the
                     response (see :py:attr:`meta`)
        """
        self.status: int = status
        self.headers = Headers(headers)
        self.body: Union[BodyTypes, None] = body
        self.reason = reason
        self.http_version = http_version
        self.request = request
        self.meta: dict[str, Any] = dict(meta) if meta is not None else {}
        """Application metadata riding along with the response — e.g. a
        backend's native response object, cache markers, timings — never
        sent on the wire and not part of :py:meth:`__eq__`. The dict is
        the response's own (the constructor copies the given mapping);
        libraries should namespace their keys."""

    @property
    def phrase(self) -> str:
        """
        The reason phrase: the explicitly set :py:attr:`reason` if there is
        one, otherwise the registry phrase of the status code, otherwise
        (for unregistered codes) an empty string.
        """
        if self.reason is not None:
            return self.reason
        try:
            return Status(self.status).phrase
        except ValueError:
            return ""

    @property
    def is_informational(self) -> bool:
        """``True`` for 1xx statuses (works for any int status)."""
        return 100 <= self.status <= 199

    @property
    def is_success(self) -> bool:
        """``True`` for 2xx statuses (works for any int status)."""
        return 200 <= self.status <= 299

    @property
    def is_redirection(self) -> bool:
        """``True`` for 3xx statuses (works for any int status)."""
        return 300 <= self.status <= 399

    @property
    def is_client_error(self) -> bool:
        """``True`` for 4xx statuses (works for any int status)."""
        return 400 <= self.status <= 499

    @property
    def is_server_error(self) -> bool:
        """``True`` for 5xx statuses (works for any int status)."""
        return 500 <= self.status <= 599

    def body_bytes(self) -> Union[bytes, None]:
        """
        The body as bytes, regardless of how it was set: bytes are
        returned as-is, a str is encoded with the Content-Type charset
        (default utf-8), a :py:class:`~action0.req.body.BodyProducer` is
        read in full.

        :return: the body bytes, ``None`` if there is no body
        """
        return _body_bytes(self.body, _charset(self.headers))

    def body_str(self) -> Union[str, None]:
        """
        The body as text, regardless of how it was set: a str is returned
        as-is, bytes are decoded with the Content-Type charset (default
        utf-8), a :py:class:`~action0.req.body.BodyProducer` is read in
        full and decoded.

        :return: the body text, ``None`` if there is no body
        """
        return _body_str(self.body, _charset(self.headers))

    def body_producer(self) -> Union[BodyProducer, None]:
        """
        The body as a streaming producer, regardless of how it was set: a
        :py:class:`~action0.req.body.BodyProducer` is returned as-is,
        bytes and str are wrapped in an in-memory
        :py:class:`~action0.req.body.BytesBody` (str encoded with the
        Content-Type charset first).

        :return: the body producer, ``None`` if there is no body
        """
        return _body_producer(self.body, _charset(self.headers))

    def body_file(self) -> Union[IO[bytes], None]:
        """
        The body as a seekable binary file object positioned at the
        start — for consumers that read from a file, like streaming
        parsers or ``gzip.GzipFile(fileobj=...)``::

            >>> import gzip
            >>> resp = Response(200, body=gzip.compress(b"report"))
            >>> gzip.GzipFile(fileobj=resp.body_file()).read()
            b'report'

        What the file is depends on the body:

        - bytes, str and :py:class:`~action0.req.body.BytesBody`: a new,
          independent in-memory file per call.
        - :py:class:`~action0.req.body.SpooledBody`: the body's own file,
          rewound (see :py:meth:`SpooledBody.file()
          <action0.req.body.SpooledBody.file>`). Closing it discards the
          data.
        - :py:class:`~action0.req.body.FileBody` over a seekable file
          object: that file object, rewound. It stays yours — neither
          the body nor :py:meth:`close` closes it.
        - ``FileBody`` over a path: the file is opened, and a
          ``FileBody`` owning the open file **replaces**
          :py:attr:`body`. Further calls return the same file, rewound,
          and :py:meth:`close` closes it. Nothing is copied.
        - any other producer (e.g. a streamed body): the stream is read
          to its end into a new ``SpooledBody``, which then **replaces**
          :py:attr:`body` — so the response stays readable, and
          :py:meth:`close` removes the temporary file. The producer it
          replaces has delivered everything and is closed (if it has a
          ``close()``), which releases its connection. This reads
          synchronously; in asynchronous code ask for a spooled body
          upfront instead.

        :return: the file object, ``None`` if there is no body
        """
        body = self.body
        if body is None:
            return None
        if isinstance(body, SpooledBody):
            return body.file()
        if isinstance(body, (bytes, str, BytesBody)):
            return io.BytesIO(_body_bytes(body, _charset(self.headers)) or b"")
        if isinstance(body, FileBody):
            found = _file_of(body)
            if found is not None:
                # for a path: the body now holding the open file, so that
                # close() releases it like everything else
                self.body, file = found
                return file
        spool = SpooledBody()
        try:
            for chunk in body.chunks():
                spool.write(chunk)
        except BaseException:
            # nothing refers to the half-filled spool: don't leave its
            # temporary file to the garbage collector
            spool.close()
            raise
        self.body = spool
        # the response no longer refers to the producer, so close() could
        # not reach it later: release what it holds now
        close = getattr(body, "close", None)
        if callable(close):
            close()
        return spool.file()

    def save_body(
        self,
        target: Union[str, "os.PathLike[str]", IO[bytes]],
        *,
        append: bool = False,
    ) -> int:
        """
        Copy the body into a file, chunk by chunk — a streamed body is
        never held in memory as a whole. See
        :py:func:`~action0.req.body.save_body` for the details.

        Example — check the response first, write afterwards, so that an
        error body never ends up in the file::

            >>> import io
            >>> download = io.BytesIO(b"first half, ")
            >>> _ = download.seek(0, io.SEEK_END)
            >>> resp = Response(206, headers={"Content-Range": "bytes 12-22/23"}, body=b"second half")
            >>> if resp.status == 206:
            ...     resp.save_body(download)
            11
            >>> download.getvalue()
            b'first half, second half'

        :param target: a path (created or truncated, or appended to with
                       ``append=True``) or a binary file object (written
                       at its current position and left open)
        :param append: for a path: write behind the existing contents
        :return: the number of bytes written; ``0`` if there is no body
        """
        return save_body(self._body_for_saving(), target, append=append)

    async def asave_body(
        self,
        target: Union[str, "os.PathLike[str]", IO[bytes]],
        *,
        append: bool = False,
    ) -> int:
        """
        :py:meth:`save_body` for asynchronous code: the body is read via
        ``achunks()`` and all file operations run in a worker thread. See
        :py:func:`~action0.req.body.asave_body`.

        :param target: a path or a binary file object, as for
                       :py:meth:`save_body`
        :param append: for a path: write behind the existing contents
        :return: the number of bytes written; ``0`` if there is no body
        """
        return await asave_body(self._body_for_saving(), target, append=append)

    def _body_for_saving(self) -> Union[bytes, BodyProducer]:
        """
        :return: the body in a form the save helpers take: a producer
                 as-is (to be streamed), everything else as bytes
        """
        body = self.body
        if body is None or isinstance(body, (bytes, str)):
            return _body_bytes(body, _charset(self.headers)) or b""
        return body

    def close(self) -> None:
        """
        Release what the body holds: the connection of a streamed body
        that was not read to its end, the temporary file of a spooled
        body. Safe to call any number of times, and a no-op for bodies
        that hold nothing (bytes, str, no body).

        A closed body can no longer be read. Note that :py:meth:`copy`
        shares the body: closing one copy closes it for all of them.

        :raises RuntimeError: if the body holds something that can only
                              be released asynchronously (an ``aclose``
                              callback still to run, a producer with
                              ``aclose()`` but no ``close()``) — use
                              :py:meth:`aclose`
        """
        body = self.body
        if _closes_async_only(body):
            raise RuntimeError("an async body cannot be closed synchronously; use aclose()")
        close = getattr(body, "close", None)
        if callable(close):
            close()

    async def aclose(self) -> None:
        """
        :py:meth:`close` for asynchronous code: awaits the body's
        ``aclose()`` if it has one, and falls back to its ``close()``.
        """
        body = self.body
        # a duck-typed, optional method. Not tested with callable(): that
        # narrows it to "returns object", which cannot be awaited
        aclose: Any = getattr(body, "aclose", None)
        if aclose is not None:
            await aclose()
            return
        close = getattr(body, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "Response":
        """
        :return: the response itself; leaving the ``with`` block closes
                 it, see :py:meth:`close`
        """
        return self

    def __exit__(
        self,
        exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        traceback: Union[TracebackType, None],
    ) -> None:
        """
        Close the response, see :py:meth:`close`.

        A body that can only be closed asynchronously makes ``close()``
        raise. Not so when the block is left by an exception: that error
        is the one to report, and it is not replaced.
        """
        if exc_type is not None and _closes_async_only(self.body):
            return
        self.close()

    async def __aenter__(self) -> "Response":
        """
        :return: the response itself; leaving the ``async with`` block
                 closes it, see :py:meth:`aclose`
        """
        return self

    async def __aexit__(
        self,
        exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        traceback: Union[TracebackType, None],
    ) -> None:
        """
        Close the response, see :py:meth:`aclose`.
        """
        await self.aclose()

    def copy(self, **overrides: Any) -> "Response":
        """
        An independent copy of this response (with its own ``Headers``
        instance and ``meta`` dict), optionally with attributes replaced.
        The body and the ``request`` reference are carried over as-is —
        in particular a :py:class:`~action0.req.body.BodyProducer` is
        shared, not copied; the ``meta`` values are shared too (shallow
        copy).

        Example::

            >>> resp = Response(200, body="ok")
            >>> resp.copy(status=404, reason="Nope")
            Response(404 Nope)

        :param overrides: any attribute accepted by the constructor
        :return: a new Response, this instance is not modified
        """
        kwargs: dict[str, Any] = {
            "status": self.status,
            "headers": self.headers,
            "body": self.body,
            "reason": self.reason,
            "http_version": self.http_version,
            "request": self.request,
            "meta": self.meta,
        }
        kwargs.update(overrides)
        return Response(**kwargs)

    def as_str(self, include_body: bool = False, separator: str = "\r\n") -> str:
        r"""
        The wire representation: the status line (with the
        :py:attr:`phrase`), the header lines and optionally the body.
        Secret header values are NOT redacted here, unlike in ``repr()``.

        :param include_body: append a blank line and the body (as text,
                             via :py:meth:`body_str`); a
                             :py:class:`~action0.req.body.BodyProducer` is
                             not consumed — a placeholder is shown instead
        :param separator: the string joining the lines (no trailing one)
        :return: e.g. ``"HTTP/1.1 404 Not Found\r\nContent-Length: 0"``
        """
        # unregistered codes have no phrase — avoid a trailing space then
        lines = [f"{self.http_version} {self.status} {self.phrase}".rstrip()]
        if self.headers:
            lines.append(self.headers.as_str(separator))
        rendered = separator.join(lines)

        if include_body and self.body is not None:
            body_text = _rendered_body(self.body, _charset(self.headers))
            rendered = f"{rendered}{separator}{separator}{body_text}"
        return rendered

    def __eq__(self, other: object) -> bool:
        """
        Responses are equal when their status, reason, headers, body and
        HTTP version are equal, each with the part's own equality
        semantics (e.g. header name casing doesn't matter). The body is
        compared as set: ``b"x"`` and ``"x"`` are different bodies. The
        ``request`` reference and the :py:attr:`meta` dict are metadata
        and not compared.

        :param other: the Response to compare with
        :return: whether the responses are equal
        """
        if isinstance(other, Response):
            return (
                self.status == other.status
                and self.reason == other.reason
                and self.http_version == other.http_version
                and self.headers == other.headers
                and self.body == other.body
            )
        return NotImplemented

    def __repr__(self) -> str:
        """
        :return: the status code and the :py:attr:`phrase`; headers and
                 body are left out, so no secret header values can leak.
                 ``str()`` falls back to this representation — the wire
                 rendering is only available explicitly via
                 :py:meth:`as_str`.
        """
        inner = f"{self.status} {self.phrase}".rstrip()
        return f"{self.__class__.__name__}({inner})"

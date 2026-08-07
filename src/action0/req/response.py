"""The HTTP response representation (:py:class:`Response`)."""

from typing import Any
from typing import Mapping
from typing import Union

from .body import BodyProducer
from .body import BodyTypes
from .body import _body_bytes
from .body import _body_producer
from .body import _body_str
from .body import _charset
from .body import _rendered_body
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
    :py:meth:`body_str` and :py:meth:`body_producer`.
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

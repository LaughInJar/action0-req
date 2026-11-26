"""The HTTP request representation (:py:class:`Request`) and the method constants."""

from enum import StrEnum
from typing import Any
from typing import Mapping
from typing import Union

from action0.url import Url
from action0.url.params import ParamTypes

from .body import BodyProducer
from .body import BodyTypes
from .body import _body_bytes
from .body import _body_producer
from .body import _body_str
from .body import _charset
from .body import _rendered_body
from .headers import Header
from .headers import Headers
from .headers import HeaderTypes

# hostname of the placeholder authority a URL is rendered behind to read off
# its request target, see _request_target()
_ANCHOR = "x"


def _request_target(url: Url) -> str:
    """
    The origin-form request target of a URL (:rfc:`9112`, section 3.2.1):
    the path (with its path params) and the query, spelled exactly as
    ``url.as_str()`` spells them — so the percent escapes of a signed URL
    arrive on the wire unchanged.

    :param url: the URL of the request
    :return: the target, always starting with a "/"
    """
    # The Url renders its own path; rebuilding it from the decoded ``path``
    # would lose the escapes ("/a%2Fb" -> "/a/b"). Behind an authority the
    # path is written as it is, whereas a URL without one gets its path
    # prefixed where it would be read as a host or a scheme ("//x" ->
    # "/.//x"), hence the placeholder; userinfo and fragment are not part
    # of the target.
    anchored = url.copy(authority=_ANCHOR, username=None, password=None, fragment=None)
    # a scheme cannot contain a "/", so this is the placeholder authority
    target = anchored.as_str().partition(f"//{_ANCHOR}")[2]
    # an empty path: "" or just "?query"
    return target if target.startswith("/") else f"/{target}"


def _host(url: Url) -> str:
    """
    The value of the ``Host`` header for a URL: hostname and port as they
    are written in the URL string — a non-ASCII hostname IDNA-encoded, an
    IPv6 address in brackets. ``url.authority`` is the readable form and
    not what goes on the wire.

    :param url: the URL of the request
    :return: e.g. ``"example.com:8443"``, "" for a URL without a hostname
    """
    authority = url.copy(
        scheme=None,
        username=None,
        password=None,
        path=None,
        path_params=None,
        query=None,
        fragment=None,
    ).as_str()
    # "//host:port", or "" for a URL without an authority
    return authority[2:]


class Method(StrEnum):
    """
    The HTTP request methods ("verbs") as string constants.

    The members are the methods defined by :rfc:`9110`, plus ``PATCH``
    (:rfc:`5789`) and ``QUERY`` (:rfc:`10008`). Being a
    :py:class:`enum.StrEnum`, every member *is* the method string:

    >>> from action0.req import Method
    >>> print(Method.GET)
    GET
    >>> Method.GET == "GET"
    True
    >>> Method("POST") is Method.POST
    True
    """

    CONNECT = "CONNECT"
    DELETE = "DELETE"
    GET = "GET"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"
    PATCH = "PATCH"
    POST = "POST"
    PUT = "PUT"
    QUERY = "QUERY"
    TRACE = "TRACE"


class Request:
    r"""
    Python representation of an HTTP request: method, URL, headers, body
    and HTTP version — every part a plain mutable attribute
    (:py:class:`~action0.url.base.Url` for the URL,
    :py:class:`~action0.req.headers.Headers` for the headers).

    Example::

        >>> req = Request("https://api.example.com/items", query={"page": 2})
        >>> req.method
        'GET'
        >>> req.url.as_str()
        'https://api.example.com/items?page=2'
        >>> req.headers["Accept"] = "application/json"
        >>> print(req.as_str(separator="\n"))
        GET /items?page=2 HTTP/1.1
        Host: api.example.com
        Accept: application/json
        >>> req
        Request(GET https://api.example.com/items?page=2)

    The URL is kept as it is written: nothing is assumed about parts it
    does not have (no default scheme), and its percent escapes and the
    spelling of its query survive up to the wire rendering.

    The body can be set as ``bytes``, ``str`` or a streaming
    :py:class:`~action0.req.body.BodyProducer` and retrieved in any of the
    three forms via :py:meth:`body_bytes`, :py:meth:`body_str` and
    :py:meth:`body_producer`, regardless of how it was set.
    """

    def __init__(
        self,
        url: Union[str, Url, None] = None,
        method: str = Method.GET,
        *,
        query: Union[ParamTypes, None] = None,
        headers: Union[HeaderTypes, None] = None,
        body: Union[BodyTypes, None] = None,
        http_version: str = "HTTP/1.1",
        meta: Union[Mapping[str, Any], None] = None,
    ) -> None:
        """
        :param url: the URL to request, as a string or an existing
                    :py:class:`~action0.url.base.Url` (which is copied, so
                    the request owns its URL); ``None`` starts from an
                    empty URL to be filled in via ``request.url``. A
                    string is taken as it is: without a scheme it is a
                    relative reference ("example.com/items" is a path,
                    not a host)
        :param method: the HTTP method, e.g. a :py:class:`Method` member or
                       any string (uppercased on the way in)
        :param query: if given, replaces the query of the given URL — like
                      the ``query`` argument of the ``Url`` constructor,
                      except that ``None`` leaves the URL's query alone
                      (the ``Url`` constructor removes it then); an empty
                      mapping or string removes it
        :param headers: the initial header lines, in any form accepted by
                        :py:class:`~action0.req.headers.Headers`
        :param body: the request body as ``bytes``, ``str`` or a streaming
                     :py:class:`~action0.req.body.BodyProducer`
        :param http_version: the protocol version rendered in the request
                             line
        :param meta: initial application metadata riding along with the
                     request (see :py:attr:`meta`)
        """
        if url is None:
            self.url = Url()
        elif isinstance(url, Url):
            # copy on ingestion (like Headers/Params) so later changes to
            # the caller's Url and to request.url are independent
            self.url = url.copy()
        else:
            self.url = Url(url)
        if query is not None:
            # the Url builds its Params itself (as a copy), with the
            # encoding its scheme calls for
            self.url.query = query

        self.method: str = method.upper()
        self.headers = Headers(headers)
        self.body: Union[BodyTypes, None] = body
        self.http_version = http_version
        self.meta: dict[str, Any] = dict(meta) if meta is not None else {}
        """Application metadata riding along with the request — correlation
        ids, tracing context, per-request knobs for custom backends, ... —
        never sent on the wire and not part of :py:meth:`__eq__`. The dict
        is the request's own (the constructor copies the given mapping);
        libraries should namespace their keys (e.g.
        ``"my-lib.correlation-id"``)."""

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

    def copy(self, **overrides: Any) -> "Request":
        """
        An independent copy of this request (with its own ``Url``,
        ``Headers`` and ``meta`` dict), optionally with attributes
        replaced. The body is carried over as-is — in particular a
        :py:class:`~action0.req.body.BodyProducer` is shared, not copied;
        the ``meta`` values are shared too (shallow copy).

        Example::

            >>> req = Request("https://api.example.com/items")
            >>> req.copy(method="POST", body="{}")
            Request(POST https://api.example.com/items)

        :param overrides: any attribute accepted by the constructor
        :return: a new Request, this instance is not modified
        """
        kwargs: dict[str, Any] = {
            "url": self.url,
            "method": self.method,
            "headers": self.headers,
            "body": self.body,
            "http_version": self.http_version,
            "meta": self.meta,
        }
        kwargs.update(overrides)
        return Request(**kwargs)

    def as_str(self, include_body: bool = False, separator: str = "\r\n") -> str:
        r"""
        The wire representation: the request line with the origin-form
        target (path and query), the header lines — with a ``Host`` header
        derived from the URL if none is set — and optionally the body.
        Secret header values are NOT redacted here, unlike in ``repr()``.

        The target is the part of ``request.url.as_str()`` between the
        authority and the fragment, character for character — percent
        escapes are neither decoded nor re-encoded — and always an
        absolute path ("/" for an empty one). The derived ``Host`` has the
        hostname as the URL string has it: IDNA-encoded if it is not
        ASCII, an IPv6 address in brackets.

        Example::

            >>> req = Request("https://bücher.example/a%2Fb?sig=x%2By&flag")
            >>> print(req.as_str(separator="\n"))
            GET /a%2Fb?sig=x%2By&flag HTTP/1.1
            Host: xn--bcher-kva.example

        :param include_body: append a blank line and the body (as text,
                             via :py:meth:`body_str`); a
                             :py:class:`~action0.req.body.BodyProducer` is
                             not consumed — a placeholder is shown instead
        :param separator: the string joining the lines (no trailing one)
        :return: e.g. ``"GET /items?page=2 HTTP/1.1\r\nHost: api.example.com"``
        """
        lines = [f"{self.method} {_request_target(self.url)} {self.http_version}"]
        host = _host(self.url)
        if Header.HOST not in self.headers and host:
            lines.append(f"Host: {host}")
        if self.headers:
            lines.append(self.headers.as_str(separator))
        rendered = separator.join(lines)

        if include_body and self.body is not None:
            body_text = _rendered_body(self.body, _charset(self.headers))
            rendered = f"{rendered}{separator}{separator}{body_text}"
        return rendered

    def __eq__(self, other: object) -> bool:
        """
        Requests are equal when their method, URL, headers, body and HTTP
        version are equal, each with the part's own equality semantics
        (e.g. header name casing doesn't matter; URLs are equal when they
        are equivalent: ``%7E`` equals ``~``, but ``/a%2Fb`` is not
        ``/a/b``). The body is compared as set: ``b"x"`` and ``"x"`` are
        different bodies. The :py:attr:`meta` dict is metadata and not
        compared.

        :param other: the Request to compare with
        :return: whether the requests are equal
        """
        if isinstance(other, Request):
            return (
                self.method == other.method
                and self.http_version == other.http_version
                and self.url == other.url
                and self.headers == other.headers
                and self.body == other.body
            )
        return NotImplemented

    def __repr__(self) -> str:
        """
        :return: the method and the URL, with the password redacted like
                 ``repr(Url)`` does; headers and body are left out, so no
                 secret header values can leak. ``str()`` falls back to
                 this representation — the wire rendering is only
                 available explicitly via :py:meth:`as_str`.
        """
        # reuse Url.__repr__ for its password redaction: "Url(...)" -> "..."
        url_repr = repr(self.url)
        redacted_url = url_repr[url_repr.find("(") + 1 : -1]
        inner = " ".join(part for part in (self.method, redacted_url) if part)
        return f"{self.__class__.__name__}({inner})"

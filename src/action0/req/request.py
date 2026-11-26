"""The HTTP request representation (:py:class:`Request`) and the method constants."""

import re
from enum import StrEnum
from types import TracebackType
from typing import Any
from typing import Mapping
from typing import Self
from typing import Union
from typing import cast
from urllib.parse import unquote_plus

from action0.url import Url
from action0.url.params import ParamTypes

from .body import BodyProducer
from .body import BodyTypes
from .body import _abody_bytes
from .body import _abody_str
from .body import _aclose_body
from .body import _aclose_on_exit
from .body import _body_bytes
from .body import _body_producer
from .body import _body_str
from .body import _charset
from .body import _close_body
from .body import _close_on_exit
from .body import _rendered_body
from .headers import _LINE_END
from .headers import Header
from .headers import Headers
from .headers import HeaderTypes
from .headers import _check_token
from .headers import _own_headers

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


# the blank line between the head of a message and its body: two line ends
# (see _LINE_END) — CRLF as on the wire, or a bare LF
_BLANK_LINE = re.compile(r"\r?\n\r?\n")

# what a Host header must not contain: it is a host and optionally a port,
# and everything here would make it — put in front of the target — the
# userinfo, the path, the query or the fragment of the URL
_NOT_A_HOST = re.compile(r"[\s/?#@\\]")


def _split_message(text: str) -> tuple[str, str, Union[str, None]]:
    """
    Take the text of an HTTP message apart — the inverse of what the
    ``as_str()`` of a request or response puts together.

    :param text: the message: a start line, header lines, and optionally
                 a blank line followed by the body
    :return: the start line, the header block and the body — ``None`` if
             there is no blank line, or nothing behind it
    """
    # a robust parser skips empty lines ahead of the start line
    # (RFC 9112, section 2.2)
    parts = _BLANK_LINE.split(text.lstrip("\r\n"), maxsplit=1)
    body = parts[1] if len(parts) > 1 and parts[1] else None
    lines = _LINE_END.split(parts[0], maxsplit=1)
    return lines[0], lines[1] if len(lines) > 1 else "", body


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
    and HTTP version — every part a mutable attribute
    (:py:class:`~action0.url.base.Url` for the URL,
    :py:class:`~action0.req.headers.Headers` for the headers; a string
    or a mapping assigned to them is converted).

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
    :py:meth:`body_producer`, regardless of how it was set
    (:py:meth:`abody_bytes` and :py:meth:`abody_str` in asynchronous
    code). A request whose body holds a resource is released with
    :py:meth:`close` or :py:meth:`aclose`, or by using the request as a
    (sync or async) context manager.

    The attributes: :py:attr:`method`, :py:attr:`url`, :py:attr:`headers`,
    :py:attr:`body`, :py:attr:`http_version`, :py:attr:`target` and
    :py:attr:`meta`.
    """

    secret_params: frozenset[str] = frozenset(
        {
            "access_token",
            "api_key",
            "apikey",
            "auth",
            "client_secret",
            "id_token",
            "key",
            "password",
            "refresh_token",
            "secret",
            "sig",
            "signature",
            "token",
            "x-amz-security-token",
            "x-amz-signature",
            "x-goog-signature",
        }
    )
    """The (casefolded) names of the query parameters whose values carry
    secrets and are shown as ``***`` by ``repr()`` — the counterpart of
    :py:attr:`Headers.secret_names <action0.req.headers.Headers.secret_names>`.
    A fragment written like a query (``#access_token=...``, as the OAuth
    implicit flow returns it) is redacted the same way. Override on a
    subclass or instance to redact more (or fewer) parameters;
    :py:meth:`copy` keeps an instance's own set. Only ``repr()``
    redacts: ``request.url`` and :py:meth:`as_str` keep the values."""

    body: Union[BodyTypes, None]
    """The body as it was set: ``bytes``, ``str``, a
    :py:class:`~action0.req.body.BodyProducer` or ``None``. Nothing else
    is a body — for a ``bytearray`` or a ``memoryview`` the accessors
    raise a :py:class:`TypeError`: pass ``bytes(...)``."""

    http_version: str
    """The protocol version rendered in the request line."""

    target: Union[str, None]
    """The request target to render instead of the one derived from the
    URL, ``None`` (the default) to derive it. :py:meth:`as_str` writes
    the origin-form (path and query) of :py:attr:`url`; the other forms
    of :rfc:`9112`, section 3.2, cannot be read off a URL and are set
    here: ``"*"`` for a server-wide ``OPTIONS``, ``"host:port"`` for
    ``CONNECT``, an absolute URL for a request sent to a proxy."""

    meta: dict[str, Any]
    """Application metadata riding along with the request — correlation
    ids, tracing context, per-request knobs for custom backends, ... —
    never sent on the wire and not part of :py:meth:`__eq__`. The dict
    is the request's own (the constructor copies the given mapping);
    libraries should namespace their keys (e.g.
    ``"my-lib.correlation-id"``)."""

    def __init__(
        self,
        url: Union[str, Url, None] = None,
        method: str = Method.GET,
        *,
        query: Union[ParamTypes, None] = None,
        headers: Union[HeaderTypes, None] = None,
        body: Union[BodyTypes, None] = None,
        http_version: str = "HTTP/1.1",
        target: Union[str, None] = None,
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
                        :py:class:`~action0.req.headers.Headers`; a
                        ``Headers`` instance is copied (keeping its class
                        and its ``secret_names``)
        :param body: the request body as ``bytes``, ``str`` or a streaming
                     :py:class:`~action0.req.body.BodyProducer`
        :param http_version: the protocol version rendered in the request
                             line
        :param target: the request target to render instead of the
                       origin-form derived from the URL (see
                       :py:attr:`target`)
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

        self.method = method
        # copied on ingestion like the URL; the setter takes it as it is
        self.headers = _own_headers(headers)
        self.body = body
        self.http_version = http_version
        self.target = target
        self.meta = dict(meta) if meta is not None else {}

    @property
    def method(self) -> str:
        """
        The HTTP method, e.g. ``"GET"``. It is uppercased whenever it is
        set — by the constructor and by an assignment alike::

            >>> req = Request("https://api.example.com/items")
            >>> req.method = "post"
            >>> req.method
            'POST'
        """
        # kept in the instance dict under its public name: that is where
        # a plain attribute lives, so pickles from before this was a
        # property still load
        return cast("str", self.__dict__["method"])

    @method.setter
    def method(self, value: str) -> None:
        self.__dict__["method"] = value.upper()

    @property
    def url(self) -> Url:
        """
        The URL of the request, a :py:class:`~action0.url.base.Url`. A
        ``Url`` that is assigned becomes the request's URL as it is
        (only the constructor copies); a string is parsed, and ``None``
        gives an empty URL — as in the constructor::

            >>> req = Request("https://api.example.com/items")
            >>> req.url = "https://api.example.com/users?page=2"
            >>> req.url.path
            '/users'

        Anything else raises a :py:class:`TypeError`.
        """
        # in the instance dict under its public name, like the method
        return cast("Url", self.__dict__["url"])

    @url.setter
    def url(self, value: Union[str, Url, None]) -> None:
        if value is None:
            value = Url()
        elif isinstance(value, str):
            value = Url(value)
        elif not isinstance(value, Url):
            raise TypeError(f"the URL must be a Url or a str, got {type(value).__name__}")
        self.__dict__["url"] = value

    @property
    def headers(self) -> Headers:
        """
        The header fields, a :py:class:`~action0.req.headers.Headers`. A
        ``Headers`` instance that is assigned becomes the request's
        headers as it is (only the constructor copies); every other form
        the ``Headers`` constructor accepts — a mapping, a list of
        tuples, a raw header block, ``None`` for none — is converted, so
        the lookups stay case-insensitive::

            >>> req = Request("https://api.example.com/items")
            >>> req.headers = {"accept": "application/json"}
            >>> req.headers["Accept"]
            'application/json'
        """
        return cast("Headers", self.__dict__["headers"])

    @headers.setter
    def headers(self, value: Union[HeaderTypes, None]) -> None:
        self.__dict__["headers"] = value if isinstance(value, Headers) else Headers(value)

    @classmethod
    def from_str(cls, text: str, *, scheme: Union[str, None] = None) -> Self:
        r"""
        Parse the text of a request — the inverse of :py:meth:`as_str`:
        the request line, the header lines and, behind a blank line, the
        body. Lines may end in ``"\r\n"`` or ``"\n"``.

        Example::

            >>> req = Request.from_str(
            ...     "POST /items?page=2 HTTP/1.1\nHost: api.example.com\n\nhello",
            ...     scheme="https",
            ... )
            >>> req
            Request(POST https://api.example.com/items?page=2)
            >>> req.headers
            Headers(Host: api.example.com)
            >>> req.body
            'hello'

        The URL is put together from the target and the ``Host`` header.
        A request does not say which scheme it was sent with, so the URL
        has none unless ``scheme`` is given. A target that is not a path
        — ``*``, the ``host:port`` of a ``CONNECT``, the absolute URL of
        a request to a proxy — is kept as :py:attr:`target`, so that
        :py:meth:`as_str` writes it again.

        The ``Host`` header stays among the headers, and
        ``Request.from_str(text).as_str(...)`` gives the text back as it
        was (with ``include_body=True`` and the separator of the text) —
        but for what a request always is here: the method is uppercased,
        and a request without a ``Host`` header whose target names the
        host (the absolute URL, the ``host:port`` of a ``CONNECT``) is
        rendered with the ``Host`` derived from it.

        The ``Host`` header has to be what it is called, a host and
        optionally a port. A value with more to it (``example.com/path``,
        ``trusted.com@evil.com``) would end up as the path or the
        userinfo of the URL, and with two ``Host`` lines it is anybody's
        guess which one counts: both are refused, as a server refuses
        them (:rfc:`9112`, section 3.2).

        :param text: the request as text; the body is whatever follows
                     the first blank line and is taken as a ``str``
                     (``None`` if there is no blank line or nothing
                     behind it)
        :param scheme: the scheme for the URL, e.g. ``"https"``; ignored
                       for a target that is an absolute URL
        :return: the request
        :raises ValueError: if the request line does not consist of a
                            method, a target and a version, or a header
                            line, the ``Host`` header or the URL is
                            invalid
        """
        start, fields, body = _split_message(text)
        parts = start.split(" ")
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"invalid request line: {start!r}")
        method, target, http_version = parts
        headers = Headers(fields)
        hosts = headers.get_all(Header.HOST)
        if len(hosts) > 1:
            raise ValueError(f"more than one Host header: {hosts!r}")
        host = hosts[0] if hosts else ""
        if _NOT_A_HOST.search(host):
            raise ValueError(
                f"invalid Host header, expected a host and optionally a port: {host!r}"
            )

        explicit: Union[str, None] = None
        if target.startswith("/"):
            # origin-form. Without a host, a path starting with "//" is
            # still a path: the (empty) authority keeps it from being
            # read as a host
            anchored = bool(host) or target.startswith("//")
            url = Url(f"//{host}{target}" if anchored else target)
            if _request_target(url) != target:
                # a spelling the Url does not keep: what was written wins
                explicit = target
        else:
            explicit = target
            if target == "*":
                url = Url(f"//{host}") if host else Url()
            elif "://" in target:
                url = Url(target)
            else:
                # authority-form: "host:port"
                url = Url(f"//{target}")
        if scheme is not None and not url.scheme:
            url.scheme = scheme

        return cls(
            url,
            method,
            headers=headers,
            body=body,
            http_version=http_version,
            target=explicit,
        )

    def body_bytes(self) -> Union[bytes, None]:
        """
        The body as bytes, regardless of how it was set: bytes are
        returned as-is, a str is encoded with the Content-Type charset
        (default utf-8), a :py:class:`~action0.req.body.BodyProducer` is
        read in full.

        A body streaming from a single-use source (a generator, a
        connection) can be read only once; a second read raises a
        :py:class:`RuntimeError`.

        :return: the body bytes, ``None`` if there is no body
        :raises LookupError: if a str body is to be encoded and Python
                             does not know the Content-Type charset
        :raises UnicodeEncodeError: if a str body cannot be encoded with
                                    the charset
        :raises RuntimeError: for a producer that cannot be read
                              (again), or only asynchronously — use
                              :py:meth:`abody_bytes` then
        """
        return _body_bytes(self.body, _charset(self.headers))

    def body_str(self) -> Union[str, None]:
        """
        The body as text, regardless of how it was set: a str is returned
        as-is, bytes are decoded with the Content-Type charset (default
        utf-8), a :py:class:`~action0.req.body.BodyProducer` is read in
        full and decoded.

        :return: the body text, ``None`` if there is no body
        :raises LookupError: if Python does not know the Content-Type
                             charset
        :raises UnicodeDecodeError: if the body is not text in the
                                    charset (binary data, say)
        :raises RuntimeError: for a producer that cannot be read
                              (again), or only asynchronously — use
                              :py:meth:`abody_str` then
        """
        return _body_str(self.body, _charset(self.headers))

    async def abody_bytes(self) -> Union[bytes, None]:
        """
        :py:meth:`body_bytes` for asynchronous code: a
        :py:class:`~action0.req.body.BodyProducer` is read via its
        ``achunks()`` — which is the only way to read an
        :py:class:`~action0.req.body.AsyncIterableBody`, and keeps a
        file-backed body from blocking the event loop.

        :return: the body bytes, ``None`` if there is no body
        :raises LookupError: as for :py:meth:`body_bytes`
        :raises UnicodeEncodeError: as for :py:meth:`body_bytes`
        :raises RuntimeError: for a producer that cannot be read (again)
        """
        return await _abody_bytes(self.body, _charset(self.headers))

    async def abody_str(self) -> Union[str, None]:
        """
        :py:meth:`body_str` for asynchronous code, see
        :py:meth:`abody_bytes`.

        :return: the body text, ``None`` if there is no body
        :raises LookupError: as for :py:meth:`body_str`
        :raises UnicodeDecodeError: as for :py:meth:`body_str`
        :raises RuntimeError: for a producer that cannot be read (again)
        """
        return await _abody_str(self.body, _charset(self.headers))

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

    def close(self) -> None:
        """
        Release what the body holds — the resource behind a streamed
        body (its ``close`` callback, a generator), the temporary file of
        a spooled one. Safe to call any number of times, and a no-op for
        bodies that hold nothing (bytes, str, no body).

        A closed body can no longer be read. Note that :py:meth:`copy`
        shares the body: closing one copy closes it for all of them.

        :raises RuntimeError: if the body holds something that can only
                              be released asynchronously — use
                              :py:meth:`aclose`
        """
        _close_body(self.body)

    async def aclose(self) -> None:
        """
        :py:meth:`close` for asynchronous code: awaits the body's
        ``aclose()`` if it has one, and falls back to its ``close()``.
        """
        await _aclose_body(self.body)

    def __enter__(self) -> Self:
        """
        :return: the request itself; leaving the ``with`` block closes
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
        Close the request, see :py:meth:`close`. When the block is left
        by an exception, a failure to close does not replace it, but is
        attached to it as a note.
        """
        _close_on_exit(self.body, exc_value, "request")

    async def __aenter__(self) -> Self:
        """
        :return: the request itself; leaving the ``async with`` block
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
        Close the request, see :py:meth:`aclose`. As in ``with``, a
        failure to close does not replace an exception the block was left
        by, but is attached to it as a note.
        """
        await _aclose_on_exit(self.body, exc_value, "request")

    def copy(self, **overrides: Any) -> Self:
        """
        An independent copy of this request (with its own ``Url``,
        ``Headers`` and ``meta`` dict), optionally with attributes
        replaced. The body is carried over as-is — in particular a
        :py:class:`~action0.req.body.BodyProducer` is shared, not copied;
        the ``meta`` values are shared too (shallow copy). The copy is of
        the same class as this request, and so are its headers. Whatever
        else has been set on this instance goes along as well — a
        :py:attr:`secret_params` of its own, so the copy redacts what
        this request redacts.

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
            "target": self.target,
            "meta": self.meta,
        }
        kwargs.update(overrides)
        duplicate = self.__class__(**kwargs)
        # the constructor has set the parts of a request; what it knows
        # nothing about is taken over (shared, like the body)
        for name, value in self.__dict__.items():
            duplicate.__dict__.setdefault(name, value)
        return duplicate

    def as_str(self, include_body: bool = False, separator: str = "\r\n") -> str:
        r"""
        The wire representation: the request line with the origin-form
        target (path and query) — or :py:attr:`target`, if that is set —,
        the header lines — with a ``Host`` header derived from the URL if
        none is set — and optionally the body. Secret header values are
        NOT redacted here, unlike in ``repr()``.

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

        The other target forms are set explicitly::

            >>> req = Request("https://example.com", "OPTIONS", target="*")
            >>> print(req.as_str(separator="\n"))
            OPTIONS * HTTP/1.1
            Host: example.com

        :param include_body: append a blank line and the body as text: a
                             str as it is, bytes decoded with the
                             Content-Type charset — what is not text in
                             it shows as U+FFFD, nothing raises; a
                             :py:class:`~action0.req.body.BodyProducer` is
                             not consumed — a placeholder is shown instead
        :param separator: the string joining the lines (no trailing one)
        :return: e.g. ``"GET /items?page=2 HTTP/1.1\r\nHost: api.example.com"``
        :raises ValueError: if the method, the target or the HTTP version
                            is empty or contains a CR, an LF or a NUL —
                            it would start a line of its own — or any
                            other whitespace: the three are separated by
                            single spaces, a space inside one of them
                            would make it two
        :raises TypeError: if the body is a ``bytearray`` or a
                           ``memoryview`` (with ``include_body``)
        """
        target = self.target if self.target is not None else _request_target(self.url)
        _check_token(self.method, "method")
        _check_token(target, "request target")
        _check_token(self.http_version, "HTTP version")
        lines = [f"{self.method} {target} {self.http_version}"]
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
        Requests are equal when their method, URL, headers, body, HTTP
        version and explicit :py:attr:`target` are equal, each with the
        part's own equality semantics (e.g. header name casing doesn't
        matter; URLs are equal when they are equivalent: ``%7E`` equals
        ``~``, but ``/a%2Fb`` is not ``/a/b``). The body is compared as
        set: ``b"x"`` and ``"x"`` are different bodies, and producers are
        equal only if they say so
        (:py:class:`~action0.req.body.BytesBody` compares its bytes; a
        stream is equal to itself alone). The :py:attr:`meta` dict is
        metadata and not compared.

        :param other: the Request to compare with
        :return: whether the requests are equal
        """
        if isinstance(other, Request):
            return (
                self.method == other.method
                and self.http_version == other.http_version
                and self.target == other.target
                and self.url == other.url
                and self.headers == other.headers
                and self.body == other.body
            )
        return NotImplemented

    def __repr__(self) -> str:
        """
        :return: the method and the URL, with the password redacted like
                 ``repr(Url)`` does and the values of the
                 :py:attr:`secret_params` query parameters (also in a
                 fragment written like a query) shown as ``***``;
                 headers and body are left out, so no secret
                 header values can leak. ``str()`` falls back to this
                 representation — the wire rendering is only available
                 explicitly via :py:meth:`as_str`.

                 >>> Request("https://user:pw@api.example.com/items?page=2&api_key=s3cr3t")
                 Request(GET https://user:***@api.example.com/items?page=2&api_key=***)
        """
        # reuse Url.__repr__ for its password redaction: "Url(...)" -> "..."
        url_repr = repr(self.url)
        redacted_url = self._redact_query(url_repr[url_repr.find("(") + 1 : -1])
        inner = " ".join(part for part in (self.method, redacted_url) if part)
        return f"{self.__class__.__name__}({inner})"

    def _redact_query(self, url: str) -> str:
        """
        Replace the values of the :py:attr:`secret_params` in a URL
        string. Done on the string, not on the Url: setting a parameter
        there would respell the rest of the query, and the repr is to
        show the URL as it is written.

        :param url: the URL string as rendered by the Url
        :return: the URL string with ``name=***`` for the secret
                 parameters, in the query and in the fragment
        """
        # neither path nor userinfo can contain a raw "?" or "#"
        main, hash_mark, fragment = url.partition("#")
        head, question_mark, query = main.partition("?")
        # a fragment can carry parameters as well: the tokens of the
        # OAuth implicit flow ("#access_token=..."), the query of a
        # client-side route ("#/callback?token=...")
        route, fragment_mark, fragment_query = fragment.partition("?")
        return (
            f"{head}{question_mark}{self._redact_pairs(query)}"
            f"{hash_mark}{self._redact_pairs(route)}"
            f"{fragment_mark}{self._redact_pairs(fragment_query)}"
        )

    def _redact_pairs(self, query: str) -> str:
        """
        :param query: ``name=value`` pairs joined by ``"&"``, as they are
                      written in the URL
        :return: the same with ``name=***`` for the
                 :py:attr:`secret_params`; a text without such pairs
                 comes back as it is
        """
        pairs: list[str] = []
        for pair in query.split("&"):
            name, equals, _ = pair.partition("=")
            if equals and unquote_plus(name).casefold() in self.secret_params:
                pair = f"{name}=***"
            pairs.append(pair)
        return "&".join(pairs)

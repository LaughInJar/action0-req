"""The :py:class:`Headers` mapping and the known header field names (:py:class:`Header`)."""

from enum import StrEnum
from typing import Iterable
from typing import Iterator
from typing import Mapping
from typing import MutableMapping
from typing import Union
from typing import cast

HeaderValue = Union[str, int, float, bool]
"""A single header value; non-strings are coerced to strings on the way in
(bools become the web-style ``"true"`` / ``"false"``)."""

# Mapping instead of dict so callers may pass any dict-ish type and, unlike
# dict, Mapping is covariant in its value type (a dict[str, str] works too)
HeaderTypes = Union[
    Iterable[tuple[str, Union[HeaderValue, Iterable[HeaderValue]]]],
    Mapping[str, Union[HeaderValue, Iterable[HeaderValue]]],
    str,
]
"""Everything that can initialize a :py:class:`Headers` instance: a raw
header block, a mapping, or an iterable of name/value(s) tuples."""


def _coerce_value(value: HeaderValue) -> str:
    """
    Coerce a single header value to its string representation.

    :param value: the value to coerce
    :return: the value as a string, bools as "true" / "false"
    """
    # bool before the plain str() fallback: bools use the web convention
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_values(value_s: Union[HeaderValue, Iterable[HeaderValue]]) -> list[str]:
    """
    Coerce a single value or an iterable of values to a list of strings.

    :param value_s: a single value or an iterable of values
    :return: all values as a list of strings
    """
    if isinstance(value_s, (str, int, float, bool)):
        return [_coerce_value(value_s)]
    return [_coerce_value(value_) for value_ in value_s]


class Headers(MutableMapping[str, str]):
    r"""
    An ordered, case-insensitive, multi-value aware mapping of HTTP header
    fields. Internally it is a list of ``(name, value)`` lines: the order
    and casing of the representation are preserved exactly, while every
    lookup matches field names case-insensitively (per :rfc:`9110` —
    ``headers["content-type"]`` and ``headers["Content-Type"]`` hit the
    same field). Names are never normalized beyond that; in particular an
    underscore is not treated as a dash.

    Headers implements :py:class:`typing.MutableMapping`: the mapping view
    (``headers[name]``, :py:meth:`get`, ``items()``, ...) works with a
    single value per name — the last one, like :py:meth:`singles`.
    Multi-value access is available through :py:meth:`get_all`,
    :py:meth:`get_values`, :py:meth:`add`, :py:meth:`as_dict` and
    :py:meth:`as_lines`.

    Example::

        >>> headers = Headers({"Content-Type": "text/html"})
        >>> headers.add("Set-Cookie", "a=1")
        >>> headers.add("set-cookie", "b=2")
        >>> headers["content-type"]
        'text/html'
        >>> headers.get_all("Set-Cookie")
        ['a=1', 'b=2']
        >>> len(headers), "SET-COOKIE" in headers
        (2, True)
        >>> headers.as_str()
        'Content-Type: text/html\r\nSet-Cookie: a=1\r\nset-cookie: b=2'

    ``repr()`` (and thus ``str()``) redacts the values of the
    :py:attr:`secret_names` fields; only :py:meth:`as_str` renders them:

        >>> headers["Authorization"] = "Bearer secret-token"
        >>> headers
        Headers(Content-Type: text/html, Set-Cookie: ***, set-cookie: ***, Authorization: ***)
    """

    secret_names: frozenset[str] = frozenset(
        {
            "authorization",
            "proxy-authorization",
            "cookie",
            "set-cookie",
            "x-api-key",
            "x-csrf-token",
        }
    )
    """The (casefolded) names of the fields whose values carry secrets and
    are shown as ``***`` by ``repr()``. Override on a subclass or instance
    to redact more (or fewer) fields."""

    def __init__(self, headers: Union[HeaderTypes, None] = None) -> None:
        r"""
        :param headers: the initial header lines, either as a raw header
                        block (lines of ``"Name: value"`` separated by
                        ``"\r\n"`` or ``"\n"``; blank lines are skipped,
                        obsolete line folding is not supported), another
                        Headers instance whose lines are copied, or as a
                        list of tuples or a dictionary. The values can be
                        single values or lists of values (one line each);
                        non-string values are coerced to strings (bools
                        become "true" / "false").
        :raises ValueError: if a line of a raw header block has no ``":"``
        """
        self._lines: list[tuple[str, str]] = []

        if isinstance(headers, Headers):
            self._lines = list(headers._lines)

        elif isinstance(headers, str):
            for line in headers.splitlines():
                if not line.strip():
                    continue
                name, sep, value = line.partition(":")
                if not sep or not name.strip():
                    raise ValueError(f"invalid header line: {line!r}")
                self.add(name.strip(), value.strip(" \t"))

        elif isinstance(headers, Mapping):
            # cast: the type checker cannot fully rule out the Iterable-of-tuples
            # union member here because a Mapping is itself an Iterable
            mapping = cast("Mapping[str, Union[HeaderValue, Iterable[HeaderValue]]]", headers)
            for name, value_s in mapping.items():
                self.add(name, value_s)

        elif isinstance(headers, Iterable):
            for name, value_s in headers:
                self.add(name, value_s)

    def __getitem__(self, name: str) -> str:
        """
        The single value of the field; if the field has multiple lines,
        the value of the last one — like :py:meth:`singles`. Use
        :py:meth:`get_all` for all values.

        :param name: the field name (case-insensitive)
        :return: the (last) value of the field
        :raises KeyError: if the field does not exist
        """
        folded = name.casefold()
        for line_name, value in reversed(self._lines):
            if line_name.casefold() == folded:
                return value
        raise KeyError(name)

    def __setitem__(self, name: str, value: Union[HeaderValue, Iterable[HeaderValue]]) -> None:
        """
        Replace all lines of the field, same as :py:meth:`set`.

        :param name: the field name (case-insensitive)
        :param value: a single value or a list of values (one line each)
        """
        self.set(name, value)

    def __delitem__(self, name: str) -> None:
        """
        Remove the field with all its lines.

        :param name: the field name (case-insensitive)
        :raises KeyError: if the field does not exist
        """
        if not self.remove(name):
            raise KeyError(name)

    def __iter__(self) -> Iterator[str]:
        """
        :return: an iterator over the distinct field names in the order of
                 their first line, each in the casing of that first line
        """
        seen: set[str] = set()
        for name, _ in self._lines:
            folded = name.casefold()
            if folded not in seen:
                seen.add(folded)
                yield name

    def __len__(self) -> int:
        """
        :return: the number of distinct field names
        """
        return len({name.casefold() for name, _ in self._lines})

    def __contains__(self, name: object) -> bool:
        """
        :param name: the field name (case-insensitive)
        :return: whether a field with this name exists
        """
        if not isinstance(name, str):
            return False
        folded = name.casefold()
        return any(line_name.casefold() == folded for line_name, _ in self._lines)

    def get_all(self, name: str) -> list[str]:
        """
        The values of all lines of the field, in representation order; use
        ``headers[name]`` or :py:meth:`get` for the single-value view and
        :py:meth:`get_values` for the comma-split element view.

        :param name: the field name (case-insensitive)
        :return: the values as a list, an empty list if the field does
                 not exist
        """
        folded = name.casefold()
        return [value for line_name, value in self._lines if line_name.casefold() == folded]

    def get_values(self, name: str) -> list[str]:
        """
        The elements of all lines of the field: like :py:meth:`get_all`,
        but each line is additionally split on ``","`` (:rfc:`9110` list
        syntax) with whitespace stripped and empty elements dropped.

        WARNING: ``Set-Cookie`` does not use the list syntax — its values
        may contain literal commas (e.g. in an ``Expires`` attribute), so
        use :py:meth:`get_all` for it.

        :param name: the field name (case-insensitive)
        :return: the elements as a list, an empty list if the field does
                 not exist
        """
        return [
            element.strip(" \t")
            for line in self.get_all(name)
            for element in line.split(",")
            if element.strip(" \t")
        ]

    def add(self, name: str, value: Union[HeaderValue, Iterable[HeaderValue]]) -> None:
        """
        Append a line for each given value at the end, keeping existing
        lines of the field.

        :param name: the field name to add (stored in the given casing)
        :param value: the value or list of values to add (one line each)
        """
        self._lines.extend((name, value_) for value_ in _coerce_values(value))

    def remove(
        self, name: str, value: Union[HeaderValue, Iterable[HeaderValue], None] = None
    ) -> list[str]:
        """
        If only a name is given all lines of this field are removed. If a
        value or a list of values is given only the matching lines are
        removed.

        :param name: the name of the field to remove (or from which lines
                     are to be removed), case-insensitive
        :param value: if given, only lines with matching value(s) are to be
                      removed, not the entire field
        :return: a list of removed values
        """
        folded = name.casefold()
        value_list = None if value is None else _coerce_values(value)

        kept: list[tuple[str, str]] = []
        removed: list[str] = []
        for line_name, line_value in self._lines:
            if line_name.casefold() == folded and (value_list is None or line_value in value_list):
                removed.append(line_value)
            else:
                kept.append((line_name, line_value))

        self._lines = kept
        return removed

    def set(self, name: str, value: Union[HeaderValue, Iterable[HeaderValue]]) -> None:
        """
        Replace all lines of the field with the value(s) given, at the
        position of the field's first line (new fields are appended at the
        end). Setting an empty list of values removes the field.

        :param name: the field name (matched case-insensitively, stored in
                     the given casing)
        :param value: a single value or a list of values (one line each)
        """
        values = _coerce_values(value)
        if not values:
            self.remove(name)
            return

        folded = name.casefold()
        new_lines: list[tuple[str, str]] = []
        inserted = False
        for line_name, line_value in self._lines:
            if line_name.casefold() == folded:
                # replace the first line of the field with the new lines,
                # drop the others
                if not inserted:
                    new_lines.extend((name, value_) for value_ in values)
                    inserted = True
            else:
                new_lines.append((line_name, line_value))
        if not inserted:
            new_lines.extend((name, value_) for value_ in values)
        self._lines = new_lines

    # narrower than the MutableMapping contract on purpose: update() accepts
    # the same input forms as the constructor (e.g. a raw header block) and
    # takes no **kwargs — header names (e.g. "Content-Type") aren't Python
    # identifiers
    def update(  # type: ignore[override]  # ty: ignore[invalid-method-override]
        self, headers: Union[HeaderTypes, None] = None
    ) -> None:
        """
        Merge the given headers into this instance: lines of fields that
        already exist are replaced (like ``dict.update``, at the position
        of the field's first line), other fields are appended. Accepts the
        same forms as the constructor (raw header block, mapping, iterable
        of tuples, Headers instance).

        :param headers: the headers to merge in
        """
        if headers is None:
            return
        other = headers if isinstance(headers, Headers) else Headers(headers)
        for name in other:
            self.set(name, other.get_all(name))

    def clear(self) -> list[tuple[str, str]]:  # type: ignore[override]  # ty: ignore[invalid-method-override]
        """
        Remove all fields, returns the removed lines (unlike
        ``MutableMapping.clear`` which returns ``None``).

        :return: the removed lines as name/value tuples
        """
        old = self._lines
        self._lines = []
        return old

    def copy(self) -> "Headers":
        """
        :return: a new Headers instance with a copy of the lines
        """
        return Headers(self)

    def sort(self) -> None:
        """
        Sort the lines in place by their (casefolded) field name. The sort
        is stable: lines of the same field keep their relative order —
        unlike :py:meth:`~action0.url.params.Params.sort`, values are
        never reordered because their order can be significant (e.g.
        ``Set-Cookie``).
        """
        self._lines.sort(key=lambda line: line[0].casefold())

    def as_str(self, separator: str = "\r\n") -> str:
        r"""
        The wire representation of the header lines, in order, with the
        original casing — secret values are NOT redacted here, unlike in
        ``repr()``.

        :param separator: the string joining the lines (no trailing one)
        :return: the header block, e.g. ``"Host: example.com\r\nAccept: */*"``
        """
        return separator.join(f"{name}: {value}" for name, value in self._lines)

    def as_lines(self) -> list[tuple[str, str]]:
        """
        :return: a copy of the representation: the header lines as
                 name/value tuples, in order, with the original casing
        """
        return list(self._lines)

    def as_dict(self) -> dict[str, list[str]]:
        """
        :return: the fields as a dictionary with the field names as keys
                 (in the casing of each field's first line) and the values
                 as lists of strings
        """
        result: dict[str, list[str]] = {}
        canonical: dict[str, str] = {}
        for name, value in self._lines:
            key = canonical.setdefault(name.casefold(), name)
            result.setdefault(key, []).append(value)
        return result

    def singles(self) -> dict[str, str]:
        """
        For those who are really sure that each field has only one line
        and do not want to bother with the lists for the values, this
        method will return only the last value for each field.

        WARNING: be aware, if the field has multiple lines, only one of
        those will be returned for the name!

        :return: a dictionary with a single value for each field name
        """
        return {name: values[-1] for name, values in self.as_dict().items()}

    def _folded(self) -> dict[str, list[str]]:
        """
        :return: the fields keyed by casefolded name for order- and
                 casing-insensitive comparison
        """
        result: dict[str, list[str]] = {}
        for name, value in self._lines:
            result.setdefault(name.casefold(), []).append(value)
        return result

    def __eq__(self, other: object) -> bool:
        """
        Headers are equal when they hold the same fields with the same
        values in the same per-field order; the order of different fields
        and the casing of the names don't matter. Plain mappings are
        converted to Headers before comparing.

        :param other: the Headers instance or mapping to compare with
        :return: whether the headers are equal
        """
        if isinstance(other, Headers):
            return self._folded() == other._folded()
        if isinstance(other, Mapping):
            # cast: the values of an arbitrary mapping are unknown to the
            # type checker; non-string values are coerced like everywhere else
            return self._folded() == Headers(cast("HeaderTypes", other))._folded()
        return NotImplemented

    def __repr__(self) -> str:
        """
        :return: the header lines in order, with the values of the
                 :py:attr:`secret_names` fields redacted as ``***``.
                 ``str()`` falls back to this representation, so only
                 :py:meth:`as_str` ever renders the secret values.
        """
        rendered = ", ".join(
            f"{name}: {'***' if name.casefold() in self.secret_names else value}"
            for name, value in self._lines
        )
        return f"{self.__class__.__name__}({rendered})"


class Header(StrEnum):
    """
    The known HTTP header field names as string constants.

    The members are the field names from the `IANA HTTP field name registry
    <https://www.iana.org/assignments/http-fields/>`_ — all ``permanent``,
    ``deprecated`` and ``provisional`` registrations as of 2026-08-07
    (``obsoleted`` ones are left out) — plus a handful of widely used
    de-facto standard ``X-…`` names that were never registered.

    Being a :py:class:`enum.StrEnum`, every member *is* the header name
    string, so it can be used wherever a plain :py:class:`str` is expected:

    >>> from action0.req import Header
    >>> print(Header.CONTENT_TYPE)
    Content-Type
    >>> Header.CONTENT_TYPE == "Content-Type"
    True
    >>> Header("ETag") is Header.ETAG
    True

    ``==`` on the enum is exact, case-sensitive string equality —
    case-insensitive header handling is the job of the header mapping, not
    of these constants.
    """

    # permanent IANA registrations
    ACCEPT = "Accept"
    ACCEPT_ADDITIONS = "Accept-Additions"
    ACCEPT_CH = "Accept-CH"
    ACCEPT_DATETIME = "Accept-Datetime"
    ACCEPT_ENCODING = "Accept-Encoding"
    ACCEPT_FEATURES = "Accept-Features"
    ACCEPT_LANGUAGE = "Accept-Language"
    ACCEPT_PATCH = "Accept-Patch"
    ACCEPT_POST = "Accept-Post"
    ACCEPT_QUERY = "Accept-Query"
    ACCEPT_RANGES = "Accept-Ranges"
    ACCEPT_SIGNATURE = "Accept-Signature"
    ACCESS_CONTROL_ALLOW_CREDENTIALS = "Access-Control-Allow-Credentials"
    ACCESS_CONTROL_ALLOW_HEADERS = "Access-Control-Allow-Headers"
    ACCESS_CONTROL_ALLOW_METHODS = "Access-Control-Allow-Methods"
    ACCESS_CONTROL_ALLOW_ORIGIN = "Access-Control-Allow-Origin"
    ACCESS_CONTROL_EXPOSE_HEADERS = "Access-Control-Expose-Headers"
    ACCESS_CONTROL_MAX_AGE = "Access-Control-Max-Age"
    ACCESS_CONTROL_REQUEST_HEADERS = "Access-Control-Request-Headers"
    ACCESS_CONTROL_REQUEST_METHOD = "Access-Control-Request-Method"
    AGE = "Age"
    ALLOW = "Allow"
    ALPN = "ALPN"
    ALTERNATES = "Alternates"
    ALT_SVC = "Alt-Svc"
    ALT_USED = "Alt-Used"
    APPLY_TO_REDIRECT_REF = "Apply-To-Redirect-Ref"
    AUTHENTICATION_CONTROL = "Authentication-Control"
    AUTHENTICATION_INFO = "Authentication-Info"
    AUTHORIZATION = "Authorization"
    AVAILABLE_DICTIONARY = "Available-Dictionary"
    A_IM = "A-IM"
    CACHE_CONTROL = "Cache-Control"
    CACHE_GROUPS = "Cache-Groups"
    CACHE_GROUP_INVALIDATION = "Cache-Group-Invalidation"
    CACHE_STATUS = "Cache-Status"
    CALDAV_TIMEZONES = "CalDAV-Timezones"
    CAL_MANAGED_ID = "Cal-Managed-ID"
    CAPSULE_PROTOCOL = "Capsule-Protocol"
    CDN_CACHE_CONTROL = "CDN-Cache-Control"
    CDN_LOOP = "CDN-Loop"
    CERT_NOT_AFTER = "Cert-Not-After"
    CERT_NOT_BEFORE = "Cert-Not-Before"
    CLEAR_SITE_DATA = "Clear-Site-Data"
    CLIENT_CERT = "Client-Cert"
    CLIENT_CERT_CHAIN = "Client-Cert-Chain"
    CLOSE = "Close"
    CONCEALED_AUTH_EXPORT = "Concealed-Auth-Export"
    CONNECTION = "Connection"
    CONTENT_DIGEST = "Content-Digest"
    CONTENT_DISPOSITION = "Content-Disposition"
    CONTENT_ENCODING = "Content-Encoding"
    CONTENT_LANGUAGE = "Content-Language"
    CONTENT_LENGTH = "Content-Length"
    CONTENT_LOCATION = "Content-Location"
    CONTENT_RANGE = "Content-Range"
    CONTENT_SECURITY_POLICY = "Content-Security-Policy"
    CONTENT_SECURITY_POLICY_REPORT_ONLY = "Content-Security-Policy-Report-Only"
    CONTENT_TYPE = "Content-Type"
    COOKIE = "Cookie"
    CROSS_ORIGIN_EMBEDDER_POLICY = "Cross-Origin-Embedder-Policy"
    CROSS_ORIGIN_EMBEDDER_POLICY_REPORT_ONLY = "Cross-Origin-Embedder-Policy-Report-Only"
    CROSS_ORIGIN_OPENER_POLICY = "Cross-Origin-Opener-Policy"
    CROSS_ORIGIN_OPENER_POLICY_REPORT_ONLY = "Cross-Origin-Opener-Policy-Report-Only"
    CROSS_ORIGIN_RESOURCE_POLICY = "Cross-Origin-Resource-Policy"
    DASL = "DASL"
    DATE = "Date"
    DAV = "DAV"
    DELTA_BASE = "Delta-Base"
    DEPRECATION = "Deprecation"
    DEPTH = "Depth"
    DESTINATION = "Destination"
    DETACHED_JWS = "Detached-JWS"
    DICTIONARY_ID = "Dictionary-ID"
    DPOP = "DPoP"
    DPOP_NONCE = "DPoP-Nonce"
    EARLY_DATA = "Early-Data"
    ETAG = "ETag"
    EXPECT = "Expect"
    EXPIRES = "Expires"
    FORWARDED = "Forwarded"
    FROM = "From"
    HOBAREG = "Hobareg"
    HOST = "Host"
    IF = "If"
    IF_MATCH = "If-Match"
    IF_MODIFIED_SINCE = "If-Modified-Since"
    IF_NONE_MATCH = "If-None-Match"
    IF_RANGE = "If-Range"
    IF_SCHEDULE_TAG_MATCH = "If-Schedule-Tag-Match"
    IF_UNMODIFIED_SINCE = "If-Unmodified-Since"
    IM = "IM"
    INCLUDE_REFERRED_TOKEN_BINDING_ID = "Include-Referred-Token-Binding-ID"
    INCREMENTAL = "Incremental"
    KEEP_ALIVE = "Keep-Alive"
    LABEL = "Label"
    LAST_EVENT_ID = "Last-Event-ID"
    LAST_MODIFIED = "Last-Modified"
    LINK = "Link"
    LINK_TEMPLATE = "Link-Template"
    LOCATION = "Location"
    LOCK_TOKEN = "Lock-Token"
    MAX_FORWARDS = "Max-Forwards"
    MEMENTO_DATETIME = "Memento-Datetime"
    METER = "Meter"
    MIME_VERSION = "MIME-Version"
    NEGOTIATE = "Negotiate"
    NEL = "NEL"
    ODATA_ENTITYID = "OData-EntityId"
    ODATA_ISOLATION = "OData-Isolation"
    ODATA_MAXVERSION = "OData-MaxVersion"
    ODATA_VERSION = "OData-Version"
    OPTIONAL_WWW_AUTHENTICATE = "Optional-WWW-Authenticate"
    ORDERING_TYPE = "Ordering-Type"
    ORIGIN = "Origin"
    ORIGIN_AGENT_CLUSTER = "Origin-Agent-Cluster"
    OSCORE = "OSCORE"
    OSLC_CORE_VERSION = "OSLC-Core-Version"
    OVERWRITE = "Overwrite"
    PING_FROM = "Ping-From"
    PING_TO = "Ping-To"
    POSITION = "Position"
    PREFER = "Prefer"
    PREFERENCE_APPLIED = "Preference-Applied"
    PRIORITY = "Priority"
    PROXY_AUTHENTICATE = "Proxy-Authenticate"
    PROXY_AUTHENTICATION_INFO = "Proxy-Authentication-Info"
    PROXY_AUTHORIZATION = "Proxy-Authorization"
    PROXY_STATUS = "Proxy-Status"
    PUBLIC_KEY_PINS = "Public-Key-Pins"
    PUBLIC_KEY_PINS_REPORT_ONLY = "Public-Key-Pins-Report-Only"
    RANGE = "Range"
    REDIRECT_REF = "Redirect-Ref"
    REFERER = "Referer"
    REFERRER_POLICY = "Referrer-Policy"
    REFRESH = "Refresh"
    REPLAY_NONCE = "Replay-Nonce"
    REPR_DIGEST = "Repr-Digest"
    RETRY_AFTER = "Retry-After"
    SCHEDULE_REPLY = "Schedule-Reply"
    SCHEDULE_TAG = "Schedule-Tag"
    SEC_FETCH_DEST = "Sec-Fetch-Dest"
    SEC_FETCH_MODE = "Sec-Fetch-Mode"
    SEC_FETCH_SITE = "Sec-Fetch-Site"
    SEC_FETCH_USER = "Sec-Fetch-User"
    SEC_PURPOSE = "Sec-Purpose"
    SEC_TOKEN_BINDING = "Sec-Token-Binding"
    SEC_WEBSOCKET_ACCEPT = "Sec-WebSocket-Accept"
    SEC_WEBSOCKET_EXTENSIONS = "Sec-WebSocket-Extensions"
    SEC_WEBSOCKET_KEY = "Sec-WebSocket-Key"
    SEC_WEBSOCKET_PROTOCOL = "Sec-WebSocket-Protocol"
    SEC_WEBSOCKET_VERSION = "Sec-WebSocket-Version"
    SERVER = "Server"
    SERVER_TIMING = "Server-Timing"
    SET_COOKIE = "Set-Cookie"
    SET_TXN = "Set-Txn"
    SIGNATURE = "Signature"
    SIGNATURE_INPUT = "Signature-Input"
    SLUG = "SLUG"
    SOAPACTION = "SoapAction"
    STATUS_URI = "Status-URI"
    STRICT_TRANSPORT_SECURITY = "Strict-Transport-Security"
    SUNSET = "Sunset"
    TCN = "TCN"
    TE = "TE"
    TIMEOUT = "Timeout"
    TOPIC = "Topic"
    TRACEPARENT = "Traceparent"
    TRACESTATE = "Tracestate"
    TRAILER = "Trailer"
    TRANSFER_ENCODING = "Transfer-Encoding"
    TTL = "TTL"
    UNENCODED_DIGEST = "Unencoded-Digest"
    UPGRADE = "Upgrade"
    URGENCY = "Urgency"
    USER_AGENT = "User-Agent"
    USE_AS_DICTIONARY = "Use-As-Dictionary"
    VARIANT_VARY = "Variant-Vary"
    VARY = "Vary"
    VIA = "Via"
    WANT_CONTENT_DIGEST = "Want-Content-Digest"
    WANT_REPR_DIGEST = "Want-Repr-Digest"
    WANT_UNENCODED_DIGEST = "Want-Unencoded-Digest"
    WWW_AUTHENTICATE = "WWW-Authenticate"
    X_CONTENT_TYPE_OPTIONS = "X-Content-Type-Options"
    X_FRAME_OPTIONS = "X-Frame-Options"
    __ = "*"

    # deprecated IANA registrations (still seen in the wild)
    ACCEPT_CHARSET = "Accept-Charset"
    CONTENT_ID = "Content-ID"
    C_PEP_INFO = "C-PEP-Info"
    DIFFERENTIAL_ID = "Differential-ID"
    EXPECT_CT = "Expect-CT"
    PRAGMA = "Pragma"
    PROTOCOL_INFO = "Protocol-Info"
    PROTOCOL_QUERY = "Protocol-Query"

    # provisional IANA registrations
    ACTIVATE_STORAGE_ACCESS = "Activate-Storage-Access"
    AMP_CACHE_TRANSFORM = "AMP-Cache-Transform"
    CMCD_OBJECT = "CMCD-Object"
    CMCD_REQUEST = "CMCD-Request"
    CMCD_SESSION = "CMCD-Session"
    CMCD_STATUS = "CMCD-Status"
    CMSD_DYNAMIC = "CMSD-Dynamic"
    CMSD_STATIC = "CMSD-Static"
    CONFIGURATION_CONTEXT = "Configuration-Context"
    CTA_COMMON_ACCESS_TOKEN = "CTA-Common-Access-Token"
    EDIINT_FEATURES = "EDIINT-Features"
    ISOLATION = "Isolation"
    PERMISSIONS_POLICY = "Permissions-Policy"
    REPEATABILITY_CLIENT_ID = "Repeatability-Client-ID"
    REPEATABILITY_FIRST_SENT = "Repeatability-First-Sent"
    REPEATABILITY_REQUEST_ID = "Repeatability-Request-ID"
    REPEATABILITY_RESULT = "Repeatability-Result"
    REPORTING_ENDPOINTS = "Reporting-Endpoints"
    SEC_FETCH_STORAGE_ACCESS = "Sec-Fetch-Storage-Access"
    SEC_GPC = "Sec-GPC"
    SURROGATE_CAPABILITY = "Surrogate-Capability"
    SURROGATE_CONTROL = "Surrogate-Control"
    TIMING_ALLOW_ORIGIN = "Timing-Allow-Origin"

    # unregistered de-facto standards
    X_API_KEY = "X-API-Key"
    X_CORRELATION_ID = "X-Correlation-Id"
    X_CSRF_TOKEN = "X-CSRF-Token"
    X_FORWARDED_FOR = "X-Forwarded-For"
    X_FORWARDED_HOST = "X-Forwarded-Host"
    X_FORWARDED_PROTO = "X-Forwarded-Proto"
    X_POWERED_BY = "X-Powered-By"
    X_REAL_IP = "X-Real-IP"
    X_REQUEST_ID = "X-Request-Id"
    X_ROBOTS_TAG = "X-Robots-Tag"

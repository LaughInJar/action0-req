"""Constants for the known HTTP status codes."""

from enum import IntEnum


class Status(IntEnum):
    """
    The known HTTP status codes as integer constants.

    The members are the codes from the `IANA HTTP status code registry
    <https://www.iana.org/assignments/http-status-codes/>`_ as of
    2026-08-07 with their registered reason phrases, plus the well-known
    ``418 I'm a Teapot`` from :rfc:`2324`. Being an
    :py:class:`enum.IntEnum`, every member *is* its numeric code:

    >>> from action0.req import Status
    >>> print(Status.NOT_FOUND)
    404
    >>> Status.NOT_FOUND == 404
    True
    >>> Status.NOT_FOUND.phrase
    'Not Found'
    >>> Status(503).phrase
    'Service Unavailable'
    >>> Status.NOT_FOUND.is_client_error
    True

    :rfc:`9110` renamed two reason phrases; the pre-9110 names remain
    available as aliases:

    >>> Status.PAYLOAD_TOO_LARGE is Status.CONTENT_TOO_LARGE
    True
    >>> Status.UNPROCESSABLE_ENTITY is Status.UNPROCESSABLE_CONTENT
    True
    """

    phrase: str
    """The registered reason phrase, e.g. ``"Not Found"`` for ``404``."""

    # the default for "phrase" is never used (every member below passes one,
    # and value lookups like Status(404) don't go through __new__ at all) —
    # it only keeps type checkers happy about such lookup calls
    def __new__(cls, value: int, phrase: str = "") -> "Status":
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.phrase = phrase
        return obj

    @property
    def is_informational(self) -> bool:
        """``True`` for the 1xx codes."""
        return 100 <= self <= 199

    @property
    def is_success(self) -> bool:
        """``True`` for the 2xx codes."""
        return 200 <= self <= 299

    @property
    def is_redirection(self) -> bool:
        """``True`` for the 3xx codes."""
        return 300 <= self <= 399

    @property
    def is_client_error(self) -> bool:
        """``True`` for the 4xx codes."""
        return 400 <= self <= 499

    @property
    def is_server_error(self) -> bool:
        """``True`` for the 5xx codes."""
        return 500 <= self <= 599

    # 1xx informational
    CONTINUE = 100, "Continue"
    SWITCHING_PROTOCOLS = 101, "Switching Protocols"
    PROCESSING = 102, "Processing"
    EARLY_HINTS = 103, "Early Hints"

    # 2xx success
    OK = 200, "OK"
    CREATED = 201, "Created"
    ACCEPTED = 202, "Accepted"
    NON_AUTHORITATIVE_INFORMATION = 203, "Non-Authoritative Information"
    NO_CONTENT = 204, "No Content"
    RESET_CONTENT = 205, "Reset Content"
    PARTIAL_CONTENT = 206, "Partial Content"
    MULTI_STATUS = 207, "Multi-Status"
    ALREADY_REPORTED = 208, "Already Reported"
    IM_USED = 226, "IM Used"

    # 3xx redirection
    MULTIPLE_CHOICES = 300, "Multiple Choices"
    MOVED_PERMANENTLY = 301, "Moved Permanently"
    FOUND = 302, "Found"
    SEE_OTHER = 303, "See Other"
    NOT_MODIFIED = 304, "Not Modified"
    # deprecated by RFC 9110 but still registered
    USE_PROXY = 305, "Use Proxy"
    TEMPORARY_REDIRECT = 307, "Temporary Redirect"
    PERMANENT_REDIRECT = 308, "Permanent Redirect"

    # 4xx client error
    BAD_REQUEST = 400, "Bad Request"
    UNAUTHORIZED = 401, "Unauthorized"
    PAYMENT_REQUIRED = 402, "Payment Required"
    FORBIDDEN = 403, "Forbidden"
    NOT_FOUND = 404, "Not Found"
    METHOD_NOT_ALLOWED = 405, "Method Not Allowed"
    NOT_ACCEPTABLE = 406, "Not Acceptable"
    PROXY_AUTHENTICATION_REQUIRED = 407, "Proxy Authentication Required"
    REQUEST_TIMEOUT = 408, "Request Timeout"
    CONFLICT = 409, "Conflict"
    GONE = 410, "Gone"
    LENGTH_REQUIRED = 411, "Length Required"
    PRECONDITION_FAILED = 412, "Precondition Failed"
    CONTENT_TOO_LARGE = 413, "Content Too Large"
    URI_TOO_LONG = 414, "URI Too Long"
    UNSUPPORTED_MEDIA_TYPE = 415, "Unsupported Media Type"
    RANGE_NOT_SATISFIABLE = 416, "Range Not Satisfiable"
    EXPECTATION_FAILED = 417, "Expectation Failed"
    # 418 is reserved as "(Unused)" in the registry; kept as the well-known
    # April-1st teapot (RFC 2324), like the stdlib's http.HTTPStatus
    IM_A_TEAPOT = 418, "I'm a Teapot"
    MISDIRECTED_REQUEST = 421, "Misdirected Request"
    UNPROCESSABLE_CONTENT = 422, "Unprocessable Content"
    LOCKED = 423, "Locked"
    FAILED_DEPENDENCY = 424, "Failed Dependency"
    TOO_EARLY = 425, "Too Early"
    UPGRADE_REQUIRED = 426, "Upgrade Required"
    PRECONDITION_REQUIRED = 428, "Precondition Required"
    TOO_MANY_REQUESTS = 429, "Too Many Requests"
    REQUEST_HEADER_FIELDS_TOO_LARGE = 431, "Request Header Fields Too Large"
    UNAVAILABLE_FOR_LEGAL_REASONS = 451, "Unavailable For Legal Reasons"

    # 5xx server error
    INTERNAL_SERVER_ERROR = 500, "Internal Server Error"
    NOT_IMPLEMENTED = 501, "Not Implemented"
    BAD_GATEWAY = 502, "Bad Gateway"
    SERVICE_UNAVAILABLE = 503, "Service Unavailable"
    GATEWAY_TIMEOUT = 504, "Gateway Timeout"
    HTTP_VERSION_NOT_SUPPORTED = 505, "HTTP Version Not Supported"
    VARIANT_ALSO_NEGOTIATES = 506, "Variant Also Negotiates"
    INSUFFICIENT_STORAGE = 507, "Insufficient Storage"
    LOOP_DETECTED = 508, "Loop Detected"
    # the registration was obsoleted in 2022, but the code is still around
    NOT_EXTENDED = 510, "Not Extended"
    NETWORK_AUTHENTICATION_REQUIRED = 511, "Network Authentication Required"

    # pre-RFC-9110 reason phrase aliases
    PAYLOAD_TOO_LARGE = CONTENT_TOO_LARGE
    UNPROCESSABLE_ENTITY = UNPROCESSABLE_CONTENT

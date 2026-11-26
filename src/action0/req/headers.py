"""The :py:class:`Headers` mapping and the known header field names (:py:class:`Header`)."""

import re
import string
from enum import StrEnum
from typing import Iterable
from typing import Iterator
from typing import Mapping
from typing import MutableMapping
from typing import Self
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


# the line separators of a raw header block. Deliberately not
# str.splitlines(): that also splits at a lone "\r", at "\x0b", "\x0c",
# "\x85", U+2028, ... — characters that are part of a value, not line ends
_LINE_END = re.compile(r"\r?\n")

# what must never be part of a header name or value: it would end the line
# (or, for NUL, the string) on the wire and let a value smuggle in a field
_UNSAFE = re.compile(r"[\r\n\x00]")

_UNSAFE_NAMES = {"\r": "a carriage return", "\n": "a line feed", "\x00": "a NUL character"}

_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _fold(name: str) -> str:
    """
    The form of a field name that lookups compare: its ASCII letters in
    lowercase, everything else as it is. That is how HTTP matches names.
    Deliberately neither ``str.casefold()`` nor ``str.lower()`` on the
    whole name: they also map characters from outside ASCII onto ASCII
    letters ("ſ" to "s", the Kelvin sign to "k"), which would make
    ``"Hoſt"`` the ``Host`` header here and a field of its own for every
    peer.

    :param name: the field name
    :return: the name to compare with
    """
    # str.lower() is only safe (and much faster) for an all-ASCII name
    return name.lower() if name.isascii() else name.translate(_ASCII_LOWER)


def _check_safe(text: str, what: str) -> None:
    """
    Refuse a text that would break out of its line in the wire format.

    :param text: the text to check
    :param what: what the text is, for the error message (e.g. "header
                 value")
    :raises ValueError: if the text contains a CR, an LF or a NUL
    """
    match = _UNSAFE.search(text)
    if match is not None:
        raise ValueError(
            f"{what} contains {_UNSAFE_NAMES[match.group()]}, "
            f"which would start a new line on the wire: {text!r}"
        )


def _check_token(text: str, what: str) -> None:
    """
    Refuse a text that would not stay in one piece on the wire: the
    method, the target and the version in a start line are separated by
    single spaces, so whitespace inside one of them makes it two.

    :param text: the text to check
    :param what: what the text is, for the error message (e.g. "method")
    :raises ValueError: if the text is empty or contains a CR, an LF, a
                        NUL or any other whitespace
    """
    if not text:
        raise ValueError(f"the {what} must not be empty")
    _check_safe(text, what)
    if any(char.isspace() for char in text):
        raise ValueError(
            f"{what} contains whitespace, which would split it in two on the wire: {text!r}"
        )


def _check_name(name: str) -> None:
    """
    Refuse a field name that would not be read as the name it is: an
    empty one, one with whitespace (``"Name : value"`` is a request
    smuggling vector, a leading blank is line folding) and one with a
    colon, which ends the name on the wire.

    :param name: the field name
    :raises ValueError: if the name is empty or contains a CR, an LF, a
                        NUL, any other whitespace or a colon
    """
    if not name:
        raise ValueError("a header name must not be empty")
    _check_safe(name, "header name")
    if any(char.isspace() for char in name):
        raise ValueError(
            "header name contains whitespace, which a recipient rejects or takes for "
            f"another field: {name!r}"
        )
    if ":" in name:
        raise ValueError(
            f"header name contains a colon, which ends the name on the wire: {name!r}"
        )


def _not_a_value(value: object) -> TypeError:
    """
    :param value: something that is not a header value
    :return: the error to raise for it
    """
    hint = ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        # str() would turn it into "b'...'", iterating it into numbers
        hint = ": decode it first"
    return TypeError(
        f"a header value must be a str, int, float or bool, got {type(value).__name__}{hint}"
    )


def _coerce_value(value: HeaderValue) -> str:
    """
    Coerce a single header value to its string representation.

    :param value: the value to coerce
    :return: the value as a string, bools as "true" / "false"
    :raises TypeError: for anything else — bytes (a header value is
                       text), ``None`` (which would be sent as the
                       string "None"), ...
    """
    # bool before the plain str() of the numbers: bools use the web
    # convention
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    raise _not_a_value(value)


def _coerce_values(value_s: Union[HeaderValue, Iterable[HeaderValue]]) -> list[str]:
    """
    Coerce a single value or an iterable of values to a list of strings.

    :param value_s: a single value or an iterable of values
    :return: all values as a list of strings
    :raises TypeError: for what is not a value (bytes, ``None``, ...),
                       as the value or as one of the values
    """
    if isinstance(value_s, (str, int, float, bool)):
        return [_coerce_value(value_s)]
    if isinstance(value_s, (bytes, bytearray, memoryview)) or not isinstance(value_s, Iterable):
        # bytes are iterables too — of numbers
        raise _not_a_value(value_s)
    return [_coerce_value(value_) for value_ in value_s]


def _lines(name: str, value_s: Union[HeaderValue, Iterable[HeaderValue]]) -> list[tuple[str, str]]:
    """
    The header lines for a field name and its value(s), checked: this is
    the one way lines get into a :py:class:`Headers` instance.

    :param name: the field name
    :param value_s: a single value or an iterable of values (one line each)
    :return: the lines as name/value tuples
    :raises TypeError: if the name is not a string or a value is not a
                       str, int, float or bool
    :raises ValueError: if the name is empty or contains whitespace or a
                        colon; if the name or a value contains a CR, an
                        LF or a NUL
    """
    if not isinstance(name, str):
        raise TypeError(f"a header name must be a str, got {type(name).__name__}: {name!r}")
    _check_name(name)
    values = _coerce_values(value_s)
    for value in values:
        _check_safe(value, f"value of the header {name!r}")
    return [(name, value) for value in values]


def _split_elements(line: str, separator: str = ",") -> Iterator[str]:
    """
    Split a header value at the commas of the list syntax (:rfc:`9110`,
    section 5.6.1). A comma inside a quoted string (``"a,b"``, with
    ``\\`` escaping the next character) is part of the element. The
    parameters of a value (``text/plain; charset=utf-8``) are split the
    same way, at ``";"``.

    :param line: the value of one header line
    :param separator: the character separating the elements
    :return: the elements as written, not stripped
    """
    start = 0
    quoted = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif quoted:
            if char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == separator:
            yield line[start:index]
            start = index + 1
    # a quote that is never closed takes the rest of the line along
    yield line[start:]


class Headers(MutableMapping[str, str]):
    r"""
    An ordered, case-insensitive, multi-value aware mapping of HTTP header
    fields. Internally it is a list of ``(name, value)`` lines: the order
    and casing of the representation are preserved exactly, while every
    lookup matches field names case-insensitively (per :rfc:`9110` —
    ``headers["content-type"]`` and ``headers["Content-Type"]`` hit the
    same field). Names are never normalized beyond that; in particular an
    underscore is not treated as a dash. Only the ASCII letters are
    matched without regard to case, as on the wire: a lookalike from
    outside ASCII (``"Hoſt"``) is a field of its own.

    Headers implements :py:class:`typing.MutableMapping`: the mapping view
    (``headers[name]``, ``headers.get(name)``, ``items()``, ...) works with a
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

    A name or a value that would break out of its line on the wire — one
    containing a carriage return, a line feed or a NUL — is refused with
    a :py:class:`ValueError`, wherever it comes from: a value taken from
    user input cannot smuggle in a field of its own. So is a name that
    would not be read as that name: an empty one, one with whitespace or
    with a colon.

        >>> headers.add("X-Trace", "abc\r\nSet-Cookie: admin=1")
        Traceback (most recent call last):
            ...
        ValueError: value of the header 'X-Trace' contains a carriage return, which would start a new line on the wire: 'abc\r\nSet-Cookie: admin=1'
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
    """The (lowercase) names of the fields whose values carry secrets and
    are shown as ``***`` by ``repr()``. Override on a subclass or instance
    to redact more (or fewer) fields; :py:meth:`copy` — and with it a
    request or response taking the headers over — keeps both the subclass
    and an instance's own set."""

    # the fields by folded name, each with the name of its first line and
    # the values of all its lines: built on the first lookup, dropped by
    # everything that changes the lines. A class attribute as the default,
    # so an instance from an older pickle has it too
    _index: Union[dict[str, tuple[str, list[str]]], None] = None

    def __init__(self, headers: Union[HeaderTypes, None] = None) -> None:
        r"""
        :param headers: the initial header lines, either as a raw header
                        block (lines of ``"Name: value"`` separated by
                        ``"\r\n"`` or ``"\n"`` — nothing else ends a
                        line; blank lines are skipped, obsolete line
                        folding is not supported), another Headers
                        instance whose lines are copied, or as a list of
                        tuples or a dictionary. The values can be single
                        values or lists of values (one line each);
                        numbers are coerced to strings (bools become
                        "true" / "false").
        :raises ValueError: if a line of a raw header block has no ``":"``
                            or its name is empty or contains whitespace
                            (also right before the ``":"`` — what
                            :rfc:`9112` tells a parser to reject); if a
                            name given in any other form is empty or
                            contains whitespace or a colon; if a name or
                            value contains a CR, an LF or a NUL
        :raises TypeError: if a value is not a str, int, float or bool
                           (bytes, ``None``, ...), an item of an iterable
                           is a string instead of a name/value tuple, or
                           the headers are none of the forms above
        """
        self._lines: list[tuple[str, str]] = []

        if isinstance(headers, Headers):
            self._lines = list(headers._lines)

        elif isinstance(headers, str):
            for line in _LINE_END.split(headers):
                if not line.strip():
                    continue
                name, sep, value = line.partition(":")
                # no whitespace anywhere in the name: "Name : value" is a
                # request smuggling vector, a leading blank is line
                # folding, and "GET http://x/ HTTP/1.1" is not a field
                if not sep or not name or any(char.isspace() for char in name):
                    raise ValueError(f"invalid header line: {line!r}")
                self.add(name, value.strip(" \t"))

        elif isinstance(headers, Mapping):
            # cast: the type checker cannot fully rule out the Iterable-of-tuples
            # union member here because a Mapping is itself an Iterable
            mapping = cast("Mapping[str, Union[HeaderValue, Iterable[HeaderValue]]]", headers)
            for name, value_s in mapping.items():
                self.add(name, value_s)

        elif isinstance(headers, Iterable):
            for item in headers:
                if isinstance(item, (str, bytes)):
                    # a two-character string would unpack into a name and
                    # a value: ["ab"] is not the header "a: b"
                    raise TypeError(
                        "expected (name, value) tuples as the items of the headers, "
                        f"got the {type(item).__name__} {item!r}"
                    )
                name, value_s = item
                self.add(name, value_s)

        elif headers is not None:
            raise TypeError(
                "headers must be a raw header block, a mapping, an iterable of "
                f"(name, value) tuples or a Headers instance, got {type(headers).__name__}"
            )

    def _fields(self) -> dict[str, tuple[str, list[str]]]:
        """
        :return: the fields by folded name, in the order of their first
                 lines: the name as that first line spells it, and the
                 values of all lines of the field. Not to be modified —
                 it is kept for the next lookup
        """
        index = self._index
        if index is None:
            index = {}
            for name, value in self._lines:
                folded = _fold(name)
                field = index.get(folded)
                if field is None:
                    index[folded] = (name, [value])
                else:
                    field[1].append(value)
            self._index = index
        return index

    def __getitem__(self, name: str) -> str:
        """
        The single value of the field; if the field has multiple lines,
        the value of the last one — like :py:meth:`singles`. Use
        :py:meth:`get_all` for all values.

        :param name: the field name (case-insensitive)
        :return: the (last) value of the field
        :raises KeyError: if the field does not exist
        """
        field = self._fields().get(_fold(name))
        if field is None:
            raise KeyError(name)
        return field[1][-1]

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
        # a list of its own: the fields may change while it is iterated
        return iter([name for name, _ in self._fields().values()])

    def __len__(self) -> int:
        """
        :return: the number of distinct field names
        """
        return len(self._fields())

    def __contains__(self, name: object) -> bool:
        """
        :param name: the field name (case-insensitive)
        :return: whether a field with this name exists
        """
        return isinstance(name, str) and _fold(name) in self._fields()

    def get_all(self, name: str) -> list[str]:
        """
        The values of all lines of the field, in representation order; use
        ``headers[name]`` or ``headers.get(name)`` for the single-value view and
        :py:meth:`get_values` for the comma-split element view.

        :param name: the field name (case-insensitive)
        :return: the values as a list, an empty list if the field does
                 not exist
        """
        field = self._fields().get(_fold(name))
        return [] if field is None else list(field[1])

    def get_values(self, name: str) -> list[str]:
        """
        The elements of all lines of the field: like :py:meth:`get_all`,
        but each line is additionally split on ``","`` (:rfc:`9110` list
        syntax) with whitespace stripped and empty elements dropped. A
        comma inside a quoted string is not a separator::

            >>> headers = Headers('Cache-Control: private="a, b", max-age=60')
            >>> headers.get_values("Cache-Control")
            ['private="a, b"', 'max-age=60']

        WARNING: only fields defined as lists can be split. ``Set-Cookie``
        is not one, and neither are the fields holding a date (``Date``,
        ``Expires``, ``Last-Modified``, ``If-Modified-Since``, ...): their
        values contain literal commas (``Thu, 01 Dec 1994 ...``), so use
        :py:meth:`get_all` for them. A URL in angle brackets (``Link``)
        may contain commas as well.

        :param name: the field name (case-insensitive)
        :return: the elements as a list, an empty list if the field does
                 not exist
        """
        return [
            element.strip(" \t")
            for line in self.get_all(name)
            for element in _split_elements(line)
            if element.strip(" \t")
        ]

    def add(self, name: str, value: Union[HeaderValue, Iterable[HeaderValue]]) -> None:
        """
        Append a line for each given value at the end, keeping existing
        lines of the field.

        :param name: the field name to add (stored in the given casing)
        :param value: the value or list of values to add (one line each)
        :raises ValueError: if the name is empty or contains whitespace
                            or a colon; if the name or a value contains
                            a CR, an LF or a NUL
        :raises TypeError: if the name is not a string or a value is not
                           a str, int, float or bool
        """
        self._lines.extend(_lines(name, value))
        self._index = None

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
        folded = _fold(name)
        value_list = None if value is None else _coerce_values(value)

        kept: list[tuple[str, str]] = []
        removed: list[str] = []
        for line_name, line_value in self._lines:
            if _fold(line_name) == folded and (value_list is None or line_value in value_list):
                removed.append(line_value)
            else:
                kept.append((line_name, line_value))

        self._lines = kept
        self._index = None
        return removed

    def set(self, name: str, value: Union[HeaderValue, Iterable[HeaderValue]]) -> None:
        """
        Replace all lines of the field with the value(s) given, at the
        position of the field's first line (new fields are appended at the
        end). Setting an empty list of values removes the field.

        :param name: the field name (matched case-insensitively, stored in
                     the given casing)
        :param value: a single value or a list of values (one line each)
        :raises ValueError: if the name is empty or contains whitespace
                            or a colon; if the name or a value contains
                            a CR, an LF or a NUL
        :raises TypeError: if the name is not a string or a value is not
                           a str, int, float or bool
        """
        lines = _lines(name, value)
        if not lines:
            self.remove(name)
            return

        folded = _fold(name)
        new_lines: list[tuple[str, str]] = []
        inserted = False
        for line_name, line_value in self._lines:
            if _fold(line_name) == folded:
                # replace the first line of the field with the new lines,
                # drop the others
                if not inserted:
                    new_lines.extend(lines)
                    inserted = True
            else:
                new_lines.append((line_name, line_value))
        if not inserted:
            new_lines.extend(lines)
        self._lines = new_lines
        self._index = None

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
        # one pass over the lines, whatever the number of fields: what
        # set() does for a single field, for all of them at once
        fields = other._fields()
        new_lines: list[tuple[str, str]] = []
        replaced: set[str] = set()
        for line_name, line_value in self._lines:
            folded = _fold(line_name)
            field = fields.get(folded)
            if field is None:
                new_lines.append((line_name, line_value))
            elif folded not in replaced:
                # the new lines take the place of the field's first line,
                # its other lines are dropped
                replaced.add(folded)
                new_lines.extend((field[0], value) for value in field[1])
        for folded, (name, values) in fields.items():
            if folded not in replaced:
                new_lines.extend((name, value) for value in values)
        self._lines = new_lines
        self._index = None

    def clear(self) -> list[tuple[str, str]]:  # type: ignore[override]  # ty: ignore[invalid-method-override]
        """
        Remove all fields, returns the removed lines (unlike
        ``MutableMapping.clear`` which returns ``None``).

        :return: the removed lines as name/value tuples
        """
        old = self._lines
        self._lines = []
        self._index = None
        return old

    def copy(self) -> Self:
        """
        :return: a new instance of the same class with a copy of the
                 lines — and of everything else set on this instance, so
                 a :py:attr:`secret_names` of its own keeps redacting
        """
        # not through the constructor: a subclass may have another
        # signature, and attributes set on the instance would be lost
        duplicate = self.__class__.__new__(self.__class__)
        duplicate.__dict__.update(self.__dict__)
        duplicate._lines = list(self._lines)
        duplicate._index = None
        return duplicate

    def sort(self) -> None:
        """
        Sort the lines in place by their (lowercase) field name. The sort
        is stable: lines of the same field keep their relative order —
        unlike :py:meth:`~action0.url.params.Params.sort`, values are
        never reordered because their order can be significant (e.g.
        ``Set-Cookie``).
        """
        self._lines.sort(key=lambda line: _fold(line[0]))
        self._index = None

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
        return {name: list(values) for name, values in self._fields().values()}

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
        :return: the fields keyed by folded name for order- and
                 casing-insensitive comparison
        """
        return {folded: values for folded, (_, values) in self._fields().items()}

    def __eq__(self, other: object) -> bool:
        """
        Headers are equal when they hold the same fields with the same
        values in the same per-field order; the order of different fields
        and the casing of the names don't matter. Plain mappings are
        converted to Headers before comparing; one that cannot be headers
        (a key that is not a string, a value that is ``None``, ...) is
        simply not equal.

        :param other: the Headers instance or mapping to compare with
        :return: whether the headers are equal
        """
        if isinstance(other, Headers):
            return self._folded() == other._folded()
        if isinstance(other, Mapping):
            try:
                # cast: the values of an arbitrary mapping are unknown to
                # the type checker; non-string values are coerced like
                # everywhere else
                converted = Headers(cast("HeaderTypes", other))
            except (TypeError, ValueError):
                # a comparison answers, it does not raise
                return False
            return self._folded() == converted._folded()
        return NotImplemented

    def __repr__(self) -> str:
        """
        :return: the header lines in order, with the values of the
                 :py:attr:`secret_names` fields redacted as ``***``.
                 ``str()`` falls back to this representation, so only
                 :py:meth:`as_str` ever renders the secret values.
        """
        rendered = ", ".join(
            f"{name}: {'***' if _fold(name) in self.secret_names else value}"
            for name, value in self._lines
        )
        return f"{self.__class__.__name__}({rendered})"


def _own_headers(headers: Union[HeaderTypes, None]) -> Headers:
    """
    The headers a request or response keeps for what it was given: always
    an instance of its own, never the caller's.

    :param headers: the headers as given, in any accepted form
    :return: a copy of a Headers instance — of the same class, with the
             same :py:attr:`~Headers.secret_names` —, a new Headers
             instance for everything else
    """
    if isinstance(headers, Headers):
        return headers.copy()
    return Headers(headers)


class Header(StrEnum):
    """
    The known HTTP header field names as string constants.

    The members are the field names from the `IANA HTTP field name registry
    <https://www.iana.org/assignments/http-fields/>`_ — all ``permanent``,
    ``deprecated`` and ``provisional`` registrations as of 2026-11-26
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
    CONNECT_UDP_BIND = "Connect-UDP-Bind"
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
    NO_VARY_SEARCH = "No-Vary-Search"
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
    PROXY_PUBLIC_ADDRESS = "Proxy-Public-Address"
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

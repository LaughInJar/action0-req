import pickle
import unittest
from typing import Any
from typing import cast

from action0.req import Header
from action0.req import Headers
from action0.req.headers import HeaderValue


class HeadersInitTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.headers.Headers.__init__`
    """

    def test_empty_init(self) -> None:
        """
        Test initialization with no headers and with None.
        """
        self.assertEqual(Headers().as_lines(), [])
        self.assertEqual(Headers(None).as_lines(), [])
        self.assertEqual(len(Headers()), 0)

    def test_mapping_init(self) -> None:
        """
        Test initialization from a mapping, with value coercion.
        """
        init: dict[str, HeaderValue] = {
            "Content-Type": "text/html",
            "Content-Length": 123,
            "X-Flag": True,
        }
        headers = Headers(init)
        self.assertEqual(
            headers.as_lines(),
            [("Content-Type", "text/html"), ("Content-Length", "123"), ("X-Flag", "true")],
        )

    def test_mapping_init_multi_value(self) -> None:
        """
        Test that a list value becomes one line per element.
        """
        headers = Headers({"Set-Cookie": ["a=1", "b=2"], "Host": "example.com"})
        self.assertEqual(
            headers.as_lines(),
            [("Set-Cookie", "a=1"), ("Set-Cookie", "b=2"), ("Host", "example.com")],
        )

    def test_tuples_init(self) -> None:
        """
        Test initialization from an iterable of tuples, order preserved.
        """
        headers = Headers([("B", "2"), ("A", "1"), ("b", "3")])
        self.assertEqual(headers.as_lines(), [("B", "2"), ("A", "1"), ("b", "3")])

    def test_raw_block_init(self) -> None:
        """
        Test initialization from a raw header block.
        """
        headers = Headers("Host: example.com\r\nAccept: */*\r\n\r\n")
        self.assertEqual(headers.as_lines(), [("Host", "example.com"), ("Accept", "*/*")])
        # plain "\n" and whitespace around the value are handled too
        headers = Headers("Host:  example.com \nX-Empty:")
        self.assertEqual(headers.as_lines(), [("Host", "example.com"), ("X-Empty", "")])

    def test_raw_block_init_invalid(self) -> None:
        """
        Test that a line without a colon (or without a name) raises.
        """
        with self.assertRaises(ValueError):
            Headers("Host example.com")
        with self.assertRaises(ValueError):
            Headers(": no name")

    def test_copy_init(self) -> None:
        """
        Test that initialization from another instance copies the lines.
        """
        original = Headers([("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")])
        clone = Headers(original)
        self.assertEqual(clone, original)
        clone.add("Set-Cookie", "c=3")
        self.assertEqual(original.get_all("Set-Cookie"), ["a=1", "b=2"])

    def test_header_constants_as_names(self) -> None:
        """
        Test that Header enum members work as field names.
        """
        init: dict[str, str] = {Header.CONTENT_TYPE: "text/html"}
        headers = Headers(init)
        self.assertEqual(headers["content-type"], "text/html")


class HeadersMappingTestCase(unittest.TestCase):
    """
    tests for the :py:class:`typing.MutableMapping` interface of
    :py:class:`action0.req.headers.Headers`
    """

    def test_getitem(self) -> None:
        """
        Test the case-insensitive single-value (last line) lookup.
        """
        headers = Headers([("Set-Cookie", "a=1"), ("Host", "example.com"), ("set-cookie", "b=2")])
        self.assertEqual(headers["Set-Cookie"], "b=2")
        self.assertEqual(headers["SET-COOKIE"], "b=2")
        self.assertEqual(headers.get("set-cookie"), "b=2")
        self.assertIsNone(headers.get("X-Missing"))
        with self.assertRaises(KeyError):
            headers["X-Missing"]

    def test_getitem_no_underscore_normalization(self) -> None:
        """
        Test that an underscore does not match a dash (and vice versa).
        """
        headers = Headers({"Content-Type": "text/html", "X_Custom": "under"})
        with self.assertRaises(KeyError):
            headers["content_type"]
        self.assertEqual(headers["x_custom"], "under")
        self.assertNotIn("X-Custom", headers)

    def test_setitem(self) -> None:
        """
        Test that assignment replaces all lines at the first line's position.
        """
        headers = Headers([("Set-Cookie", "a=1"), ("Host", "example.com"), ("set-cookie", "b=2")])
        headers["SET-COOKIE"] = "c=3"
        self.assertEqual(headers.as_lines(), [("SET-COOKIE", "c=3"), ("Host", "example.com")])
        headers["Accept"] = "*/*"
        self.assertEqual(headers.as_lines()[-1], ("Accept", "*/*"))

    def test_delitem(self) -> None:
        """
        Test that deletion removes all lines of the field.
        """
        headers = Headers([("Set-Cookie", "a=1"), ("Host", "example.com"), ("set-cookie", "b=2")])
        del headers["set-cookie"]
        self.assertEqual(headers.as_lines(), [("Host", "example.com")])
        with self.assertRaises(KeyError):
            del headers["set-cookie"]

    def test_contains(self) -> None:
        """
        Test the case-insensitive membership check.
        """
        headers = Headers({"Content-Type": "text/html"})
        self.assertIn("content-type", headers)
        self.assertIn("CONTENT-TYPE", headers)
        self.assertNotIn("Accept", headers)
        self.assertNotIn(42, headers)

    def test_iter_and_len(self) -> None:
        """
        Test that iteration yields distinct names in first-line order and
        first-line casing.
        """
        headers = Headers([("SET-COOKIE", "a=1"), ("Host", "example.com"), ("set-cookie", "b=2")])
        self.assertEqual(list(headers), ["SET-COOKIE", "Host"])
        self.assertEqual(len(headers), 2)
        self.assertEqual(list(headers.items()), [("SET-COOKIE", "b=2"), ("Host", "example.com")])


class HeadersMethodsTestCase(unittest.TestCase):
    """
    tests for the explicit accessors of :py:class:`action0.req.headers.Headers`
    """

    def test_add_and_get_all(self) -> None:
        """
        Test that add() appends lines and get_all() returns them in order.
        """
        headers = Headers()
        headers.add("Set-Cookie", "a=1")
        headers.add("Host", "example.com")
        headers.add("set-cookie", ["b=2", "c=3"])
        headers.add("X-Nothing", [])
        self.assertEqual(headers.get_all("SET-COOKIE"), ["a=1", "b=2", "c=3"])
        self.assertEqual(headers.get_all("X-Missing"), [])
        self.assertNotIn("X-Nothing", headers)

    def test_get_values(self) -> None:
        """
        Test the RFC 9110 comma-splitting element view.
        """
        headers = Headers("Vary: Accept-Encoding, Accept-Language\nVary: Cookie")
        self.assertEqual(headers.get_all("Vary"), ["Accept-Encoding, Accept-Language", "Cookie"])
        self.assertEqual(
            headers.get_values("vary"), ["Accept-Encoding", "Accept-Language", "Cookie"]
        )
        # whitespace is stripped, empty elements are dropped
        self.assertEqual(Headers({"Via": "a ,, b"}).get_values("Via"), ["a", "b"])

    def test_remove(self) -> None:
        """
        Test removing whole fields and single values.
        """
        headers = Headers([("Set-Cookie", "a=1"), ("Host", "example.com"), ("set-cookie", "b=2")])
        self.assertEqual(headers.remove("set-cookie", "b=2"), ["b=2"])
        self.assertEqual(headers.remove("SET-COOKIE"), ["a=1"])
        self.assertEqual(headers.remove("X-Missing"), [])
        self.assertEqual(headers.as_lines(), [("Host", "example.com")])

    def test_set_empty_removes(self) -> None:
        """
        Test that setting an empty list of values removes the field.
        """
        headers = Headers({"Accept": "*/*"})
        headers.set("accept", [])
        self.assertEqual(headers.as_lines(), [])

    def test_update(self) -> None:
        """
        Test that update() replaces existing fields and appends new ones.
        """
        headers = Headers([("Set-Cookie", "a=1"), ("Host", "example.com"), ("set-cookie", "b=2")])
        headers.update({"set-cookie": ["x=9"], "Accept": "*/*"})
        self.assertEqual(
            headers.as_lines(),
            [("set-cookie", "x=9"), ("Host", "example.com"), ("Accept", "*/*")],
        )
        # all constructor forms work, e.g. a raw header block
        headers.update("Host: other.example")
        self.assertEqual(headers["host"], "other.example")

    def test_clear(self) -> None:
        """
        Test that clear() empties the instance and returns the old lines.
        """
        headers = Headers({"Host": "example.com"})
        self.assertEqual(headers.clear(), [("Host", "example.com")])
        self.assertEqual(len(headers), 0)

    def test_copy(self) -> None:
        """
        Test that copy() returns an independent clone.
        """
        headers = Headers({"Host": "example.com"})
        clone = headers.copy()
        clone["Host"] = "other.example"
        self.assertEqual(headers["Host"], "example.com")
        self.assertEqual(clone["Host"], "other.example")

    def test_sort(self) -> None:
        """
        Test that sort() orders by name but keeps per-field line order.
        """
        headers = Headers([("Set-Cookie", "b=2"), ("Accept", "*/*"), ("set-cookie", "a=1")])
        headers.sort()
        self.assertEqual(
            headers.as_lines(),
            [("Accept", "*/*"), ("Set-Cookie", "b=2"), ("set-cookie", "a=1")],
        )

    def test_as_str(self) -> None:
        """
        Test the wire representation.
        """
        headers = Headers([("Host", "example.com"), ("Accept", "*/*")])
        self.assertEqual(headers.as_str(), "Host: example.com\r\nAccept: */*")
        self.assertEqual(headers.as_str(separator="\n"), "Host: example.com\nAccept: */*")
        self.assertEqual(Headers().as_str(), "")

    def test_as_str_round_trip(self) -> None:
        """
        Test that parsing and re-rendering a header block is lossless.
        """
        block = "Host: example.com\r\nSET-COOKIE: a=1\r\nset-cookie: b=2"
        self.assertEqual(Headers(block).as_str(), block)

    def test_as_dict_and_singles(self) -> None:
        """
        Test the dictionary views.
        """
        headers = Headers([("SET-COOKIE", "a=1"), ("Host", "example.com"), ("set-cookie", "b=2")])
        self.assertEqual(
            headers.as_dict(), {"SET-COOKIE": ["a=1", "b=2"], "Host": ["example.com"]}
        )
        self.assertEqual(headers.singles(), {"SET-COOKIE": "b=2", "Host": "example.com"})


class HeadersEqReprTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.headers.Headers.__eq__` and
    :py:meth:`action0.req.headers.Headers.__repr__`
    """

    def test_eq_ignores_name_order_and_casing(self) -> None:
        """
        Test that field order and name casing don't matter for equality.
        """
        self.assertEqual(Headers("A: 1\nB: 2"), Headers("b: 2\nA: 1"))
        self.assertEqual(Headers({"Content-Length": 5}), Headers({"CONTENT-LENGTH": "5"}))

    def test_eq_respects_value_order(self) -> None:
        """
        Test that the per-field line order matters for equality.
        """
        self.assertNotEqual(
            Headers({"Set-Cookie": ["a=1", "b=2"]}), Headers({"Set-Cookie": ["b=2", "a=1"]})
        )
        self.assertNotEqual(Headers({"A": "1"}), Headers({"A": "1", "B": "2"}))

    def test_eq_with_mapping(self) -> None:
        """
        Test that plain mappings are converted before comparing.
        """
        self.assertEqual(Headers({"Content-Length": "5"}), {"content-length": 5})
        self.assertNotEqual(Headers({"Content-Length": "5"}), {"content-length": "6"})
        self.assertNotEqual(Headers(), "Host: example.com")

    def test_repr_redacts_secrets(self) -> None:
        """
        Test that repr()/str() redact secret values while as_str() keeps them.
        """
        headers = Headers(
            [("Content-Type", "text/html"), ("AUTHORIZATION", "Bearer token"), ("Cookie", "a=1")]
        )
        self.assertEqual(
            repr(headers), "Headers(Content-Type: text/html, AUTHORIZATION: ***, Cookie: ***)"
        )
        self.assertEqual(str(headers), repr(headers))
        self.assertNotIn("Bearer token", repr(headers))
        self.assertIn("Bearer token", headers.as_str())

    def test_repr_secret_names_overridable(self) -> None:
        """
        Test that the redaction set can be extended per instance.
        """
        headers = Headers({"X-Internal-Token": "secret"})
        headers.secret_names = headers.secret_names | {"x-internal-token"}
        self.assertEqual(repr(headers), "Headers(X-Internal-Token: ***)")


class HeaderTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.headers.Header`
    """

    def test_members_are_strings(self) -> None:
        """
        Test that members are real strings usable wherever a str is expected.
        """
        self.assertIsInstance(Header.CONTENT_TYPE, str)
        self.assertEqual(Header.CONTENT_TYPE, "Content-Type")
        self.assertEqual(str(Header.CONTENT_TYPE), "Content-Type")
        self.assertEqual(f"{Header.CONTENT_TYPE}", "Content-Type")
        self.assertEqual("Accept".join(["<", ">"]), "<" + Header.ACCEPT + ">")

    def test_lookup_by_value(self) -> None:
        """
        Test that members can be looked up from the header name string.
        """
        self.assertIs(Header("ETag"), Header.ETAG)
        self.assertIs(Header("WWW-Authenticate"), Header.WWW_AUTHENTICATE)
        with self.assertRaises(ValueError):
            Header("No-Such-Header")

    def test_common_members(self) -> None:
        """
        Test a sample of members from each registry section.
        """
        # permanent registrations
        self.assertEqual(Header.AUTHORIZATION, "Authorization")
        self.assertEqual(Header.CONTENT_LENGTH, "Content-Length")
        self.assertEqual(Header.SET_COOKIE, "Set-Cookie")
        self.assertEqual(Header.X_FRAME_OPTIONS, "X-Frame-Options")
        # deprecated registrations
        self.assertEqual(Header.PRAGMA, "Pragma")
        self.assertEqual(Header.ACCEPT_CHARSET, "Accept-Charset")
        # provisional registrations
        self.assertEqual(Header.PERMISSIONS_POLICY, "Permissions-Policy")
        # unregistered de-facto standards
        self.assertEqual(Header.X_FORWARDED_FOR, "X-Forwarded-For")
        self.assertEqual(Header.X_REQUEST_ID, "X-Request-Id")

    def test_registry_size(self) -> None:
        """
        Test that the full IANA registry made it into the enum.
        """
        # 190 permanent + 8 deprecated + 23 provisional + 10 de-facto
        self.assertEqual(len(Header), 231)

    def test_values_unique(self) -> None:
        """
        Test that no two members share a header name (which would silently
        turn one of them into an alias).
        """
        # __members__ and not the enum itself: iterating an enum skips
        # the aliases, which are exactly what this is looking for
        members = Header.__members__
        self.assertEqual(len(members), len(Header))
        values = [member.value for member in members.values()]
        self.assertEqual(len(values), len(set(values)))


class HeadersValidationTestCase(unittest.TestCase):
    """
    tests for what :py:class:`action0.req.headers.Headers` refuses to
    take in
    """

    def test_line_breaks_in_values_are_refused(self) -> None:
        """
        Test that a value cannot smuggle in a header line of its own:
        a CR, an LF or a NUL raises, whichever way the value comes in,
        and nothing is added.
        """
        headers = Headers({"Accept": "*/*"})
        for value in ("1\r\nSet-Cookie: evil=1", "1\nX: y", "1\rX: y", "a\x00b"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    headers.add("X-A", value)
                with self.assertRaises(ValueError):
                    headers.set("X-A", value)
                with self.assertRaises(ValueError):
                    headers["X-A"] = value
                with self.assertRaises(ValueError):
                    headers.update({"X-A": value})
                with self.assertRaises(ValueError):
                    Headers({"X-A": value})
                with self.assertRaises(ValueError):
                    Headers([("X-A", ["fine", value])])
        self.assertEqual(headers.as_str(), "Accept: */*")

    def test_line_breaks_in_names_are_refused(self) -> None:
        """
        Test that a field name cannot break out of its line either.
        """
        for name in ("X-A: 1\r\nEvil", "X\nY", "X\rY", "X\x00"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    Headers([(name, "1")])
                with self.assertRaises(ValueError):
                    Headers().set(name, "1")

    def test_error_names_the_field_and_the_character(self) -> None:
        """
        Test that the error says which field it is about and what is
        wrong with the value.
        """
        with self.assertRaisesRegex(ValueError, "'X-Trace' contains a line feed"):
            Headers().add("X-Trace", "a\nb")

    def test_names_must_be_strings(self) -> None:
        """
        Test that a name that is not a string raises a TypeError instead
        of failing later in a lookup.
        """
        with self.assertRaises(TypeError):
            Headers(cast("Any", [(1, "x")]))
        with self.assertRaises(TypeError):
            Headers().add(cast("Any", b"X-A"), "x")

    def test_bytes_values_are_refused(self) -> None:
        """
        Test that bytes are not taken for a list of values — iterating
        them gives numbers — and not for a value either.
        """
        values: list[Any] = [b"ab", bytearray(b"ab"), memoryview(b"ab")]
        for value in values:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    Headers({"X": value})
                with self.assertRaises(TypeError):
                    Headers().add("X", ["fine", value])
                with self.assertRaises(TypeError):
                    Headers("X: ab").remove("X", value)

    def test_string_items_are_refused(self) -> None:
        """
        Test that a string among the items of an iterable is not unpacked
        into a name and a value ("ab" is not the header "a: b").
        """
        with self.assertRaises(TypeError):
            Headers(cast("Any", ["ab", "cd"]))
        with self.assertRaises(TypeError):
            Headers(cast("Any", [b"ab"]))


class HeadersRawBlockTestCase(unittest.TestCase):
    """
    tests for the parsing of a raw header block
    """

    def test_only_cr_lf_and_lf_end_a_line(self) -> None:
        """
        Test that the characters str.splitlines() also takes for line
        ends are part of the value.
        """
        for char in ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "):
            with self.subTest(char=repr(char)):
                headers = Headers(f"X-A: a{char}b\r\nX-B: c\nX-C: d")
                self.assertEqual(
                    headers.as_lines(), [("X-A", f"a{char}b"), ("X-B", "c"), ("X-C", "d")]
                )

    def test_lone_carriage_return_is_refused(self) -> None:
        """
        Test that a CR without an LF is neither a line end nor allowed
        in a value.
        """
        with self.assertRaises(ValueError):
            Headers("A: 1\rB: 2")

    def test_whitespace_in_the_name_is_refused(self) -> None:
        """
        Test that a line is not a header line if there is whitespace
        before the colon, inside the name or ahead of it: the request
        smuggling vector of RFC 9112, a request line, a folded line.
        """
        for block in ("Name : v", "Name\t: v", "GET http://x/ HTTP/1.1", " folded: v", ": v"):
            with self.subTest(block=block):
                with self.assertRaises(ValueError):
                    Headers(f"A: 1\n{block}")

    def test_value_whitespace_is_stripped(self) -> None:
        """
        Test that the optional whitespace around a value is not part of
        it, while the name is taken as written.
        """
        self.assertEqual(Headers("A:1\nB: \t 2 \t").as_lines(), [("A", "1"), ("B", "2")])


class HeadersGetValuesTestCase(unittest.TestCase):
    """
    tests for the list splitting of
    :py:meth:`action0.req.headers.Headers.get_values`
    """

    def test_quoted_commas_are_not_separators(self) -> None:
        """
        Test that a comma inside a quoted string belongs to its element.
        """
        headers = Headers('Cache-Control: private="a,b", max-age=1\nCache-Control: no-store')
        self.assertEqual(
            headers.get_values("cache-control"), ['private="a,b"', "max-age=1", "no-store"]
        )

    def test_escaped_quote_does_not_end_the_string(self) -> None:
        """
        Test that a backslash-escaped quote inside a quoted string is
        not its end.
        """
        headers = Headers([("X-List", r'"a\",b", c')])
        self.assertEqual(headers.get_values("X-List"), [r'"a\",b"', "c"])
        headers = Headers([("X-List", r'"a\\", b')])
        self.assertEqual(headers.get_values("X-List"), [r'"a\\"', "b"])

    def test_unclosed_quote_takes_the_rest(self) -> None:
        """
        Test that a quote that is never closed does not lose anything:
        the rest of the line is one element.
        """
        self.assertEqual(Headers([("X-List", 'a, "b, c')]).get_values("X-List"), ["a", '"b, c'])

    def test_empty_elements_are_dropped(self) -> None:
        """
        Test that empty elements are still dropped.
        """
        self.assertEqual(Headers([("X-List", " a ,, b,")]).get_values("X-List"), ["a", "b"])


class HeadersCopyTestCase(unittest.TestCase):
    """
    tests for what :py:meth:`action0.req.headers.Headers.copy` keeps
    """

    def test_copy_keeps_the_subclass(self) -> None:
        """
        Test that the copy of a subclass is of that subclass, and with
        it redacts what the subclass redacts.
        """

        class _Strict(Headers):
            secret_names = Headers.secret_names | {"x-token"}

        headers = _Strict({"X-Token": "s3cr3t"})
        duplicate = headers.copy()
        self.assertIs(type(duplicate), _Strict)
        self.assertEqual(repr(duplicate), "_Strict(X-Token: ***)")

    def test_copy_keeps_the_secret_names_of_the_instance(self) -> None:
        """
        Test that a redaction set assigned to an instance travels with
        its copies.
        """
        headers = Headers({"X-Token": "s3cr3t"})
        headers.secret_names = headers.secret_names | {"x-token"}
        self.assertEqual(repr(headers.copy()), "Headers(X-Token: ***)")
        self.assertEqual(repr(headers.copy().copy()), "Headers(X-Token: ***)")

    def test_copy_does_not_need_the_constructor(self) -> None:
        """
        Test that a subclass with a constructor of its own can be
        copied, and that the lines of the copy are its own.
        """

        class _Tagged(Headers):
            def __init__(self, tag: str) -> None:
                super().__init__()
                self.tag = tag

        headers = _Tagged("t")
        headers.add("A", "1")
        duplicate = headers.copy()
        duplicate.add("B", "2")
        self.assertEqual(duplicate.tag, "t")
        self.assertEqual(headers.as_lines(), [("A", "1")])
        self.assertEqual(duplicate.as_lines(), [("A", "1"), ("B", "2")])

    def test_pickle_round_trip(self) -> None:
        """
        Test that headers survive pickling, with order and casing.
        """
        headers = Headers("B: 2\na: 1\nA: 3")
        restored = pickle.loads(pickle.dumps(headers))
        self.assertEqual(restored.as_lines(), headers.as_lines())


class HeadersMappingMixinTestCase(unittest.TestCase):
    """
    tests for the methods :py:class:`action0.req.headers.Headers`
    inherits from ``MutableMapping``, which work on the single-value view
    """

    def setUp(self) -> None:
        self.headers = Headers("A: 1\nB: 2\na: 3")

    def test_views(self) -> None:
        """
        Test keys(), values() and items(): one entry per field, with the
        last value.
        """
        self.assertEqual(list(self.headers.keys()), ["A", "B"])
        self.assertEqual(list(self.headers.values()), ["3", "2"])
        self.assertEqual(list(self.headers.items()), [("A", "3"), ("B", "2")])
        self.assertIn("b", self.headers.keys())

    def test_get(self) -> None:
        """
        Test get() with and without a default.
        """
        self.assertEqual(self.headers.get("a"), "3")
        self.assertIsNone(self.headers.get("C"))
        self.assertEqual(self.headers.get("C", "x"), "x")

    def test_pop(self) -> None:
        """
        Test that pop() returns the last value and removes the field
        with all its lines.
        """
        self.assertEqual(self.headers.pop("A"), "3")
        self.assertEqual(self.headers.as_lines(), [("B", "2")])
        self.assertEqual(self.headers.pop("A", "gone"), "gone")
        with self.assertRaises(KeyError):
            self.headers.pop("A")

    def test_popitem(self) -> None:
        """
        Test that popitem() takes the first field.
        """
        self.assertEqual(self.headers.popitem(), ("A", "3"))
        self.assertEqual(self.headers.as_lines(), [("B", "2")])

    def test_setdefault(self) -> None:
        """
        Test that setdefault() leaves an existing field alone and adds a
        missing one.
        """
        self.assertEqual(self.headers.setdefault("b", "x"), "2")
        self.assertEqual(self.headers.setdefault("C", "x"), "x")
        self.assertEqual(self.headers.get_all("C"), ["x"])

    def test_update_with_nothing(self) -> None:
        """
        Test that update() without headers changes nothing.
        """
        self.headers.update()
        self.headers.update(None)
        self.assertEqual(self.headers.as_lines(), [("A", "1"), ("B", "2"), ("a", "3")])


class HeadersEqRobustnessTestCase(unittest.TestCase):
    """
    tests for comparing :py:class:`action0.req.headers.Headers` with
    mappings that cannot be headers
    """

    def test_unconvertible_mappings_are_unequal(self) -> None:
        """
        Test that a comparison answers instead of raising.
        """
        headers = Headers({"A": "1"})
        others: list[dict[Any, Any]] = [
            {"A": None},
            {1: "x"},
            {"A": b"1"},
            {"A": "1\r\nB: 2"},
        ]
        for other in others:
            with self.subTest(other=other):
                self.assertFalse(headers == other)
                self.assertTrue(headers != other)

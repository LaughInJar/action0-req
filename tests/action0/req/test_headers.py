import unittest

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
        # 187 permanent + 8 deprecated + 23 provisional + 10 de-facto
        self.assertEqual(len(Header), 228)

    def test_values_unique(self) -> None:
        """
        Test that no two members share a header name (which would silently
        turn one of them into an alias).
        """
        values = [member.value for member in Header]
        self.assertEqual(len(values), len(set(values)))

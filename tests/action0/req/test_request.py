import unittest

from action0.req import BytesBody
from action0.req import Headers
from action0.req import Method
from action0.req import Request
from action0.url import Url


class RequestInitTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.request.Request.__init__`
    """

    def test_defaults(self) -> None:
        """
        Test the empty request: GET, empty URL, empty headers, no body.
        """
        req = Request()
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.url.as_str(), "")
        self.assertEqual(len(req.headers), 0)
        self.assertIsNone(req.body)
        self.assertEqual(req.http_version, "HTTP/1.1")

    def test_url_from_string(self) -> None:
        """
        Test that a URL string is parsed into a Url instance.
        """
        req = Request("https://api.example.com/items?page=1")
        self.assertIsInstance(req.url, Url)
        self.assertEqual(req.url.path, "/items")
        self.assertEqual(req.url.query["page"], "1")

    def test_url_instance_is_copied(self) -> None:
        """
        Test that a passed Url is copied, not shared.
        """
        url = Url("https://api.example.com/items")
        req = Request(url)
        req.url.path = "/other"
        self.assertEqual(url.path, "/items")
        url.path = "/changed"
        self.assertEqual(req.url.path, "/other")

    def test_query_kwarg_replaces_url_query(self) -> None:
        """
        Test that the query keyword replaces the URL's query, like the
        Url constructor does.
        """
        req = Request("https://api.example.com/items?page=1", query={"q": "x"})
        self.assertEqual(req.url.as_str(), "https://api.example.com/items?q=x")

    def test_method_uppercased(self) -> None:
        """
        Test that the method is uppercased and Method members work.
        """
        self.assertEqual(Request(method="post").method, "POST")
        self.assertEqual(Request(method=Method.DELETE).method, "DELETE")

    def test_headers_coerced_and_copied(self) -> None:
        """
        Test that headers accept any Headers input form and instances are
        copied.
        """
        req = Request(headers={"Accept": "*/*"})
        self.assertEqual(req.headers["accept"], "*/*")

        headers = Headers({"Accept": "*/*"})
        req = Request(headers=headers)
        req.headers.add("X-More", "1")
        self.assertNotIn("X-More", headers)


class RequestBodyTestCase(unittest.TestCase):
    """
    tests for the body accessors of :py:class:`action0.req.request.Request`
    """

    def test_no_body(self) -> None:
        """
        Test that all accessors return None without a body.
        """
        req = Request()
        self.assertIsNone(req.body_bytes())
        self.assertIsNone(req.body_str())
        self.assertIsNone(req.body_producer())

    def test_bytes_body(self) -> None:
        """
        Test the accessors for a body set as bytes.
        """
        req = Request(body=b"hello")
        self.assertEqual(req.body_bytes(), b"hello")
        self.assertEqual(req.body_str(), "hello")
        producer = req.body_producer()
        assert producer is not None
        self.assertEqual(producer.as_bytes(), b"hello")

    def test_str_body(self) -> None:
        """
        Test the accessors for a body set as text.
        """
        req = Request(body="hällo")
        self.assertEqual(req.body_str(), "hällo")
        self.assertEqual(req.body_bytes(), "hällo".encode())
        producer = req.body_producer()
        assert producer is not None
        self.assertEqual(producer.as_bytes(), "hällo".encode())

    def test_producer_body(self) -> None:
        """
        Test the accessors for a body set as a BodyProducer.
        """
        producer = BytesBody(b"hello")
        req = Request(body=producer)
        self.assertIs(req.body_producer(), producer)
        self.assertEqual(req.body_bytes(), b"hello")
        self.assertEqual(req.body_str(), "hello")

    def test_charset_from_content_type(self) -> None:
        """
        Test that encoding and decoding honor the Content-Type charset.
        """
        req = Request(body="hällo", headers={"Content-Type": "text/plain; charset=latin-1"})
        self.assertEqual(req.body_bytes(), "hällo".encode("latin-1"))

        req = Request(
            body="hällo".encode("latin-1"),
            headers={"Content-Type": 'text/plain; charset="latin-1"'},
        )
        self.assertEqual(req.body_str(), "hällo")


class RequestAsStrTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.request.Request.as_str`
    """

    def test_request_line_and_derived_host(self) -> None:
        """
        Test the origin-form target and the Host header derived from the URL.
        """
        req = Request("https://api.example.com/items?page=2")
        self.assertEqual(req.as_str(), "GET /items?page=2 HTTP/1.1\r\nHost: api.example.com")

    def test_host_includes_port(self) -> None:
        """
        Test that a non-default port ends up in the derived Host header.
        """
        req = Request("https://api.example.com:8443/")
        self.assertEqual(req.as_str(), "GET / HTTP/1.1\r\nHost: api.example.com:8443")

    def test_existing_host_not_duplicated(self) -> None:
        """
        Test that an explicitly set Host header wins over the URL's.
        """
        req = Request("https://api.example.com/", headers={"host": "other.example"})
        self.assertEqual(req.as_str(), "GET / HTTP/1.1\r\nhost: other.example")

    def test_empty_path_renders_slash(self) -> None:
        """
        Test that an empty path becomes the "/" target.
        """
        req = Request("https://api.example.com")
        self.assertEqual(req.as_str(), "GET / HTTP/1.1\r\nHost: api.example.com")

    def test_include_body(self) -> None:
        """
        Test that include_body appends a blank line and the text body.
        """
        req = Request(
            "https://api.example.com/items",
            Method.POST,
            headers={"Content-Type": "application/json"},
            body='{"a": 1}',
        )
        self.assertEqual(
            req.as_str(include_body=True, separator="\n"),
            "POST /items HTTP/1.1\n"
            "Host: api.example.com\n"
            "Content-Type: application/json\n"
            "\n"
            '{"a": 1}',
        )

    def test_include_body_does_not_consume_producer(self) -> None:
        """
        Test that a BodyProducer body is shown as a placeholder only.
        """
        req = Request("https://api.example.com/", body=BytesBody(b"data"))
        rendered = req.as_str(include_body=True)
        self.assertIn("<BytesBody>", rendered)
        self.assertNotIn("data", rendered)


class RequestCopyEqReprTestCase(unittest.TestCase):
    """
    tests for copy(), __eq__ and __repr__ of
    :py:class:`action0.req.request.Request`
    """

    def test_copy_is_independent(self) -> None:
        """
        Test that the copy has its own Url and Headers.
        """
        req = Request("https://api.example.com/items", headers={"Accept": "*/*"})
        clone = req.copy()
        self.assertEqual(clone, req)
        clone.url.path = "/other"
        clone.headers["Accept"] = "text/html"
        self.assertEqual(req.url.path, "/items")
        self.assertEqual(req.headers["Accept"], "*/*")

    def test_copy_with_overrides(self) -> None:
        """
        Test that constructor keywords override the copied attributes.
        """
        req = Request("https://api.example.com/items")
        clone = req.copy(method="POST", body=b"data", query={"a": "1"})
        self.assertEqual(clone.method, "POST")
        self.assertEqual(clone.body, b"data")
        self.assertEqual(clone.url.as_str(), "https://api.example.com/items?a=1")
        self.assertEqual(req.method, "GET")
        self.assertIsNone(req.body)

    def test_eq(self) -> None:
        """
        Test equality across the parts and inequality with other types.
        """
        self.assertEqual(
            Request("https://e.com/?a=1&b=2", headers={"Accept": "*/*"}, body=b"x"),
            Request("https://e.com/?b=2&a=1", headers={"ACCEPT": "*/*"}, body=b"x"),
        )
        self.assertNotEqual(Request(body=b"x"), Request(body="x"))
        self.assertNotEqual(Request(method="GET"), Request(method="POST"))
        self.assertNotEqual(Request(), "GET  HTTP/1.1")

    def test_repr_redacts_url_password(self) -> None:
        """
        Test that repr() shows method and URL but never the URL password
        nor any header values.
        """
        req = Request(
            "https://user:secret@api.example.com/items",
            headers={"Authorization": "Bearer token"},
        )
        self.assertEqual(repr(req), "Request(GET https://user:***@api.example.com/items)")
        self.assertEqual(str(req), repr(req))
        self.assertNotIn("secret", repr(req))
        self.assertNotIn("token", repr(req))


class MethodTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.request.Method`
    """

    def test_members_are_strings(self) -> None:
        """
        Test that members are real strings usable wherever a str is expected.
        """
        self.assertIsInstance(Method.GET, str)
        self.assertEqual(Method.GET, "GET")
        self.assertEqual(str(Method.GET), "GET")
        self.assertEqual(f"{Method.POST}", "POST")

    def test_lookup_by_value(self) -> None:
        """
        Test that members can be looked up from the method string.
        """
        self.assertIs(Method("POST"), Method.POST)
        with self.assertRaises(ValueError):
            Method("FETCH")

    def test_all_methods(self) -> None:
        """
        Test that exactly the RFC 9110 + PATCH + QUERY methods exist.
        """
        self.assertEqual(
            sorted(member.value for member in Method),
            [
                "CONNECT",
                "DELETE",
                "GET",
                "HEAD",
                "OPTIONS",
                "PATCH",
                "POST",
                "PUT",
                "QUERY",
                "TRACE",
            ],
        )


class MetaTestCase(unittest.TestCase):
    """
    tests for the :py:attr:`Request.meta` application metadata
    """

    def test_defaults_to_an_empty_dict(self) -> None:
        """
        Test that every request has its own empty meta dict.
        """
        request = Request("https://example.com/")
        self.assertEqual(request.meta, {})
        request.meta["my-lib.key"] = "value"
        self.assertEqual(Request("https://example.com/").meta, {})

    def test_constructor_copies_the_given_mapping(self) -> None:
        """
        Test that later changes to the source mapping don't leak in.
        """
        source = {"my-lib.key": "value"}
        request = Request("https://example.com/", meta=source)
        source["my-lib.key"] = "changed"
        self.assertEqual(request.meta, {"my-lib.key": "value"})

    def test_copy_gets_its_own_meta_dict(self) -> None:
        """
        Test that copies carry the entries but not the dict identity.
        """
        request = Request("https://example.com/", meta={"my-lib.key": "value"})
        copied = request.copy()
        copied.meta["my-lib.key"] = "changed"
        self.assertEqual(request.meta, {"my-lib.key": "value"})
        overridden = request.copy(meta={"other": 1})
        self.assertEqual(overridden.meta, {"other": 1})

    def test_meta_is_not_part_of_equality(self) -> None:
        """
        Test that meta is metadata, like the docstring promises.
        """
        plain = Request("https://example.com/")
        tagged = Request("https://example.com/", meta={"my-lib.key": "value"})
        self.assertEqual(plain, tagged)

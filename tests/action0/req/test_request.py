import asyncio
import pickle
import unittest
from collections.abc import AsyncIterator
from typing import Any
from typing import cast

from action0.req import AsyncIterableBody
from action0.req import BytesBody
from action0.req import Headers
from action0.req import IterableBody
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

    def test_query_kwarg_none_keeps_url_query(self) -> None:
        """
        Test that query=None means "not given" and keeps the query of the
        URL — unlike the Url constructor, which removes it — while an
        empty mapping or string removes it.
        """
        url = "https://api.example.com/items?page=1"
        self.assertEqual(Request(url, query=None).url.as_str(), url)
        self.assertEqual(Url(url, query=None).as_str(), "https://api.example.com/items")
        self.assertEqual(Request(url, query={}).url.as_str(), "https://api.example.com/items")
        self.assertEqual(Request(url, query="").url.as_str(), "https://api.example.com/items")

    def test_query_kwarg_string_is_read_like_the_url_reads_it(self) -> None:
        """
        Test that a query string is parsed with the encoding of the URL's
        scheme, like the Url constructor does: form-encoded ("+" is a
        space) for the web schemes only.
        """
        web = Request("https://api.example.com/items", query="q=a+b")
        self.assertEqual(web.url.query["q"], "a b")
        other = Request("myapp://host/items", query="q=a+b")
        self.assertEqual(other.url.query["q"], "a+b")
        self.assertEqual(other.url.query, Url("myapp://host/items", query="q=a+b").query)

    def test_query_kwarg_is_copied(self) -> None:
        """
        Test that a Params instance given as query is not shared with the
        request's URL.
        """
        params = Url("https://api.example.com/?a=1").query
        req = Request("https://api.example.com/items", query=params)
        req.url.query["a"] = "2"
        self.assertEqual(params["a"], "1")

    def test_query_kwarg_invalid_value(self) -> None:
        """
        Test that a parameter value the URL cannot hold is rejected.
        """
        # typed as Any: the type checkers would refuse the value already
        query: Any = {"a": None}
        with self.assertRaises(TypeError):
            Request("https://api.example.com/items", query=query)

    def test_no_default_scheme(self) -> None:
        """
        Test that nothing is assumed about a URL string without a scheme:
        it stays a relative reference.
        """
        req = Request("example.com/items")
        self.assertEqual(req.url.scheme, "")
        self.assertIsNone(req.url.hostname)
        self.assertEqual(req.url.as_str(), "example.com/items")
        self.assertEqual(Request("/items?page=1").url.as_str(), "/items?page=1")

    def test_url_is_kept_as_written(self) -> None:
        """
        Test that the percent escapes and the spelling of the query of the
        given URL are preserved.
        """
        url = "https://api.example.com/a%2Fb/%7Ec?sig=x%2By%3D&flag&q=a%20b"
        self.assertEqual(Request(url).url.as_str(), url)
        self.assertEqual(Request(Url(url)).url.as_str(), url)

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

    def test_target_is_spelled_like_the_url(self) -> None:
        """
        Test that the target is the URL's own rendering of path, path
        params and query: escapes are neither decoded nor re-encoded.
        """
        cases = {
            # an escaped "/" is not a segment separator
            "https://e.com/a%2Fb/c": "/a%2Fb/c",
            # unreserved characters stay escaped, invalid UTF-8 stays as it is
            "https://e.com/%7Euser/%e4": "/%7Euser/%e4",
            # the query of a signed URL: escapes, "%20" and a flag without "="
            "https://e.com/x?sig=a%2Bb%3D&flag&q=a%20b&&c=d+e": (
                "/x?sig=a%2Bb%3D&flag&q=a%20b&&c=d+e"
            ),
            # path params, also on an inner segment
            "https://e.com/a;v=1/b;w=2?q=1": "/a;v=1/b;w=2?q=1",
            # an empty first segment must not be prefixed ("/.//")
            "https://e.com//double/slash": "//double/slash",
            "https://e.com/a:b": "/a:b",
            # characters that cannot be in a URL are encoded by the Url
            "https://e.com/a b/ü?q=ü": "/a%20b/%C3%BC?q=%C3%BC",
            # a "+" in the query of a scheme without form encoding
            "myapp://e.com/x?a=b+c%20d": "/x?a=b+c%20d",
        }
        for url, target in cases.items():
            with self.subTest(url=url):
                req = Request(url)
                self.assertEqual(req.as_str(), f"GET {target} HTTP/1.1\r\nHost: e.com")
                # ... which is what the URL itself shows behind the authority
                self.assertTrue(req.url.as_str().endswith(f"e.com{target}"))

    def test_target_of_modified_url(self) -> None:
        """
        Test that the target follows modifications of the URL, encoded the
        way the Url encodes them.
        """
        req = Request("https://e.com/x?sig=a%2Bb&flag")
        req.url.query["n"] = "a b"
        self.assertEqual(req.as_str(), "GET /x?sig=a%2Bb&flag=&n=a+b HTTP/1.1\r\nHost: e.com")
        req.url.path = "/my file;v1"
        req.url.query = None
        # a ";" in the last segment would start the path params
        self.assertEqual(req.url.as_str(), "https://e.com/my%20file%3Bv1")
        self.assertEqual(req.as_str(), "GET /my%20file%3Bv1 HTTP/1.1\r\nHost: e.com")

    def test_target_is_an_absolute_path(self) -> None:
        """
        Test that the target always starts with a "/": for an empty path
        (with and without a query) and for a relative one.
        """
        cases = {
            "https://e.com?x=1": "GET /?x=1 HTTP/1.1\r\nHost: e.com",
            "https://e.com/?": "GET / HTTP/1.1\r\nHost: e.com",
            "": "GET / HTTP/1.1",
            "/items?page=1": "GET /items?page=1 HTTP/1.1",
            "items/5": "GET /items/5 HTTP/1.1",
            "//double/slash": "GET /slash HTTP/1.1\r\nHost: double",
        }
        for url, rendered in cases.items():
            with self.subTest(url=url):
                self.assertEqual(Request(url).as_str(), rendered)

    def test_target_of_url_without_host(self) -> None:
        """
        Test that the path of a URL without a host is not prefixed the way
        the URL string has to be to round-trip.
        """
        req = Request()
        req.url.path = "//double/slash"
        self.assertEqual(req.url.as_str(), "/.//double/slash")
        self.assertEqual(req.as_str(), "GET //double/slash HTTP/1.1")
        req.url.path = "a:b"
        self.assertEqual(req.url.as_str(), "./a:b")
        self.assertEqual(req.as_str(), "GET /a:b HTTP/1.1")

    def test_target_has_no_userinfo_and_fragment(self) -> None:
        """
        Test that neither the credentials nor the fragment of the URL end
        up in the request line or the Host header.
        """
        req = Request("https://user:secret@e.com:8443/x?a=1#frag")
        self.assertEqual(req.as_str(), "GET /x?a=1 HTTP/1.1\r\nHost: e.com:8443")

    def test_host_is_idna_encoded(self) -> None:
        """
        Test that a non-ASCII hostname is IDNA-encoded in the Host header,
        as in the URL string.
        """
        req = Request("https://bücher.example:8443/x")
        self.assertEqual(req.url.authority, "bücher.example:8443")
        self.assertEqual(req.as_str(), "GET /x HTTP/1.1\r\nHost: xn--bcher-kva.example:8443")

    def test_host_ipv6_in_brackets(self) -> None:
        """
        Test that an IPv6 address keeps its brackets in the Host header.
        """
        self.assertEqual(Request("http://[::1]/x").as_str(), "GET /x HTTP/1.1\r\nHost: [::1]")
        self.assertEqual(
            Request("http://[::1]:8080/x").as_str(), "GET /x HTTP/1.1\r\nHost: [::1]:8080"
        )

    def test_no_host_without_hostname(self) -> None:
        """
        Test that no Host header is derived from a URL without an
        authority or with an empty one.
        """
        for url in ("/items", "items", "sqlite:///data.db", "https://user:secret@/x"):
            with self.subTest(url=url):
                self.assertNotIn("Host", Request(url).as_str())

    def test_as_str_does_not_modify_url(self) -> None:
        """
        Test that rendering leaves the request's URL untouched.
        """
        url = "https://user:secret@e.com/a%2Fb;p=1?flag#frag"
        req = Request(url)
        req.as_str()
        self.assertEqual(req.url.as_str(), url)

    def test_url_subclass(self) -> None:
        """
        Test that a Url subclass with a constructor of its own can be
        rendered.
        """

        class ApiUrl(Url):
            def __init__(self, resource: str) -> None:
                super().__init__(f"https://api.example.com/v1/{resource}")

        req = Request(ApiUrl("items"))
        self.assertIsInstance(req.url, ApiUrl)
        self.assertEqual(req.as_str(), "GET /v1/items HTTP/1.1\r\nHost: api.example.com")

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

    def test_eq_compares_equivalent_urls(self) -> None:
        """
        Test that requests are equal when their URLs are equivalent, not
        when the decoded parts happen to match.
        """
        self.assertEqual(Request("https://E.com/%7Ea"), Request("https://e.com/~a"))
        self.assertEqual(Request("https://e.com?a=b%20c"), Request("https://e.com?a=b+c"))
        # an escaped "/" is part of a segment, a raw one separates segments
        self.assertNotEqual(Request("https://e.com/a%2Fb"), Request("https://e.com/a/b"))
        self.assertNotEqual(Request("//e.com/a"), Request("https://e.com/a"))

    def test_copy_keeps_url_as_written(self) -> None:
        """
        Test that a copy has the URL in the same spelling.
        """
        url = "https://user:secret@e.com/a%2Fb?sig=x%2By&flag"
        clone = Request(url).copy(method="POST")
        self.assertEqual(clone.url.as_str(), url)
        self.assertEqual(clone.as_str(), "POST /a%2Fb?sig=x%2By&flag HTTP/1.1\r\nHost: e.com")

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


class RequestMethodPropertyTestCase(unittest.TestCase):
    """
    tests for :py:attr:`action0.req.request.Request.method` as a property
    """

    def test_assignment_uppercases(self) -> None:
        """
        Test that the method is uppercased by an assignment like by the
        constructor, so the rendering and the comparison agree.
        """
        req = Request("https://a/")
        req.method = "post"
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.as_str(), "POST / HTTP/1.1\r\nHost: a")
        self.assertEqual(req, Request("https://a/", "POST"))

    def test_method_member_is_stored_as_a_plain_string(self) -> None:
        """
        Test that a Method member comes out as the plain string.
        """
        req = Request("https://a/", Method.PATCH)
        self.assertIs(type(req.method), str)
        self.assertEqual(req.method, "PATCH")

    def test_pickle_round_trip(self) -> None:
        """
        Test that a request survives pickling — and that the method is
        kept where a plain attribute lives, so a pickle from before it
        was a property still loads.
        """
        req = Request(
            "https://api.example.com/a%2Fb?x=1",
            "POST",
            headers={"Accept": "*/*"},
            body=b"data",
            target="*",
            meta={"k": "v"},
        )
        restored = pickle.loads(pickle.dumps(req))
        self.assertEqual(restored, req)
        self.assertEqual(restored.meta, {"k": "v"})
        self.assertEqual(restored.as_str(), req.as_str())
        self.assertEqual(vars(req)["method"], "POST")

        old = Request.__new__(Request)
        old.__dict__.update(vars(Request("https://a/", "PUT")))
        self.assertEqual(old.method, "PUT")


class RequestTargetTestCase(unittest.TestCase):
    """
    tests for the explicit request target of
    :py:class:`action0.req.request.Request`
    """

    def test_default_is_the_origin_form(self) -> None:
        """
        Test that without a target the origin-form is derived as before.
        """
        req = Request("https://e.com/a?b=1")
        self.assertIsNone(req.target)
        self.assertEqual(req.as_str(), "GET /a?b=1 HTTP/1.1\r\nHost: e.com")

    def test_asterisk_form(self) -> None:
        """
        Test the server-wide OPTIONS: the target is "*", the Host still
        comes from the URL.
        """
        req = Request("https://e.com", "OPTIONS", target="*")
        self.assertEqual(req.as_str(), "OPTIONS * HTTP/1.1\r\nHost: e.com")

    def test_authority_form(self) -> None:
        """
        Test the target of a CONNECT request.
        """
        req = Request("//e.com:443", "CONNECT", target="e.com:443")
        self.assertEqual(req.as_str(), "CONNECT e.com:443 HTTP/1.1\r\nHost: e.com:443")

    def test_absolute_form(self) -> None:
        """
        Test the target of a request to a proxy.
        """
        url = "http://e.com/a?b=1"
        self.assertEqual(Request(url, target=url).as_str(), f"GET {url} HTTP/1.1\r\nHost: e.com")

    def test_target_is_a_plain_attribute(self) -> None:
        """
        Test that the target can be set and removed later.
        """
        req = Request("https://e.com/a")
        req.target = "*"
        self.assertEqual(req.as_str(), "GET * HTTP/1.1\r\nHost: e.com")
        req.target = None
        self.assertEqual(req.as_str(), "GET /a HTTP/1.1\r\nHost: e.com")

    def test_target_is_part_of_copy_and_equality(self) -> None:
        """
        Test that a copy keeps the target, that it can be overridden,
        and that requests differing in it are not equal.
        """
        req = Request("https://e.com", "OPTIONS", target="*")
        self.assertEqual(req.copy().target, "*")
        self.assertEqual(req.copy(), req)
        self.assertIsNone(req.copy(target=None).target)
        self.assertNotEqual(req.copy(target=None), req)


class RequestRenderingSafetyTestCase(unittest.TestCase):
    """
    tests for what :py:meth:`action0.req.request.Request.as_str` refuses
    to render, and for what it renders whatever the body is
    """

    def test_line_breaks_in_the_request_line_are_refused(self) -> None:
        """
        Test that no part of the request line can start a line of its
        own.
        """
        requests = [
            Request("https://a/", "GET / HTTP/1.1\r\nX: y"),
            Request("https://a/", target="/\r\nX: y"),
            Request("https://a/", http_version="HTTP/1.1\nX: y"),
            Request("https://a/", target="/\x00"),
        ]
        for req in requests:
            with self.subTest(req=req.method):
                with self.assertRaises(ValueError):
                    req.as_str()

    def test_binary_body_is_rendered_with_replacements(self) -> None:
        """
        Test that a body that is not text does not make the rendering
        fail: what cannot be decoded shows as U+FFFD.
        """
        req = Request("https://a/", "POST", body=b"\x1f\x8b\x08ok")
        rendered = req.as_str(include_body=True)
        self.assertTrue(rendered.endswith("\r\n\r\n\x1f�\x08ok"))

    def test_unknown_charset_is_rendered_as_utf8(self) -> None:
        """
        Test that a charset Python does not know does not make the
        rendering fail either, while the accessor still says so.
        """
        req = Request(
            "https://a/",
            "POST",
            headers={"Content-Type": "text/plain; charset=bogus"},
            body="grüße".encode(),
        )
        self.assertTrue(req.as_str(include_body=True).endswith("\r\n\r\ngrüße"))
        with self.assertRaises(LookupError):
            req.body_str()

    def test_charset_behind_another_parameter(self) -> None:
        """
        Test that the charset is found among several Content-Type
        parameters, whatever its position and quoting.
        """
        for content_type in (
            'text/plain; format=flowed; charset="latin-1"',
            "text/plain; charset=latin-1; format=flowed",
            "text/plain;format=flowed;CHARSET=latin-1",
        ):
            with self.subTest(content_type=content_type):
                req = Request("https://a/", headers={"Content-Type": content_type}, body="ä")
                self.assertEqual(req.body_bytes(), b"\xe4")
        # a parameter without a value, and no charset at all
        req = Request("https://a/", headers={"Content-Type": "text/plain; flag"}, body="ä")
        self.assertEqual(req.body_bytes(), "ä".encode())


class RequestHeadersOwnershipTestCase(unittest.TestCase):
    """
    tests for what a request keeps of the headers and the class it is
    given
    """

    def test_headers_subclass_is_kept(self) -> None:
        """
        Test that a Headers subclass — with its redaction set — survives
        the constructor and copy(), as a copy of its own.
        """

        class _Strict(Headers):
            secret_names = Headers.secret_names | {"x-token"}

        headers = _Strict({"X-Token": "s3cr3t"})
        req = Request("https://a/", headers=headers)
        self.assertIs(type(req.headers), _Strict)
        self.assertIsNot(req.headers, headers)
        self.assertEqual(repr(req.headers), "_Strict(X-Token: ***)")
        self.assertEqual(repr(req.copy().headers), "_Strict(X-Token: ***)")
        req.headers["A"] = "1"
        self.assertNotIn("A", headers)

    def test_instance_secret_names_are_kept(self) -> None:
        """
        Test that a redaction set assigned to the headers instance
        travels into the request and its copies.
        """
        headers = Headers({"X-Token": "s3cr3t"})
        headers.secret_names = headers.secret_names | {"x-token"}
        req = Request("https://a/", headers=headers)
        self.assertEqual(repr(req.headers), "Headers(X-Token: ***)")
        self.assertEqual(repr(req.copy().headers), "Headers(X-Token: ***)")

    def test_copy_of_a_subclass(self) -> None:
        """
        Test that copy() returns an instance of the request's own class.
        """

        class _Traced(Request):
            pass

        self.assertIs(type(_Traced("https://a/").copy(method="POST")), _Traced)

    def test_bytes_bodies_compare_by_content(self) -> None:
        """
        Test that requests with equal BytesBody bodies are equal.
        """
        self.assertEqual(
            Request("https://a/", body=BytesBody(b"x")),
            Request("https://a/", body=BytesBody(b"x")),
        )
        self.assertNotEqual(
            Request("https://a/", body=BytesBody(b"x")),
            Request("https://a/", body=BytesBody(b"y")),
        )


class RequestReprSecretsTestCase(unittest.TestCase):
    """
    tests for the redaction of secret query parameters in the repr of
    :py:class:`action0.req.request.Request`
    """

    def test_secret_params_are_redacted(self) -> None:
        """
        Test that the values of the known secret parameters show as
        "***" — matched case-insensitively and with the name decoded —
        while everything else stays as it is written.
        """
        req = Request("https://a/p?x=1&api_key=SECRET&flag&API_KEY=two&sig=x%2By&to%6Ben=t#f?g")
        self.assertEqual(
            repr(req),
            "Request(GET https://a/p?x=1&api_key=***&flag&API_KEY=***&sig=***&to%6Ben=***#f?g)",
        )
        for secret in ("SECRET", "two", "x%2By"):
            self.assertNotIn(secret, repr(req))

    def test_only_the_repr_redacts(self) -> None:
        """
        Test that the URL and the wire rendering keep the values.
        """
        req = Request("https://a/p?token=abc")
        self.assertEqual(req.url.as_str(), "https://a/p?token=abc")
        self.assertEqual(req.as_str(), "GET /p?token=abc HTTP/1.1\r\nHost: a")

    def test_urls_without_secrets_are_untouched(self) -> None:
        """
        Test URLs without a query, with a "?" in the fragment only, and
        with a secret name that has no value.
        """
        self.assertEqual(repr(Request("https://a/p")), "Request(GET https://a/p)")
        self.assertEqual(
            repr(Request("https://a/p#page=1?x")), "Request(GET https://a/p#page=1?x)"
        )
        self.assertEqual(repr(Request("https://a/p#section")), "Request(GET https://a/p#section)")
        self.assertEqual(repr(Request("https://a/p?token")), "Request(GET https://a/p?token)")

    def test_secret_params_are_overridable(self) -> None:
        """
        Test that the set can be extended on an instance or a subclass.
        """
        req = Request("https://a/p?session=abc&token=t")
        req.secret_params = frozenset({"session"})
        self.assertEqual(repr(req), "Request(GET https://a/p?session=***&token=t)")

        class _Strict(Request):
            secret_params = Request.secret_params | {"session"}

        self.assertEqual(
            repr(_Strict("https://a/p?session=abc&token=t")),
            "_Strict(GET https://a/p?session=***&token=***)",
        )


class RequestFromStrTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.request.Request.from_str`
    """

    def test_parts(self) -> None:
        """
        Test that every part of the text ends up where it belongs.
        """
        req = Request.from_str(
            "POST /items?page=2 HTTP/1.0\r\nHost: api.example.com\r\nAccept: */*\r\n\r\nline1\r\n\r\nline2"
        )
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.http_version, "HTTP/1.0")
        self.assertEqual(req.url.as_str(), "//api.example.com/items?page=2")
        self.assertEqual(req.headers.as_lines(), [("Host", "api.example.com"), ("Accept", "*/*")])
        self.assertEqual(req.body, "line1\r\n\r\nline2")
        self.assertIsNone(req.target)

    def test_scheme(self) -> None:
        """
        Test that the scheme is applied where the text has none.
        """
        req = Request.from_str("GET /a HTTP/1.1\nHost: e.com", scheme="https")
        self.assertEqual(req.url.as_str(), "https://e.com/a")
        self.assertEqual(req, Request("https://e.com/a", headers={"Host": "e.com"}))

    def test_no_body(self) -> None:
        """
        Test that a text without a blank line, or with nothing behind
        it, has no body.
        """
        self.assertIsNone(Request.from_str("GET / HTTP/1.1\r\nHost: a").body)
        self.assertIsNone(Request.from_str("GET / HTTP/1.1\r\nHost: a\r\n\r\n").body)
        self.assertIsNone(Request.from_str("GET / HTTP/1.1").body)

    def test_round_trip(self) -> None:
        """
        Test that parsing what as_str() renders — and rendering it again
        — gives the same text, for every target form and separator.
        """
        requests = [
            Request("https://api.example.com/items?page=2", headers={"Accept": "*/*"}),
            Request("https://bücher.example/a%2Fb?sig=x%2By&flag", "POST", body="payload"),
            Request("https://[::1]:8080/x", body="a\n\nb"),
            Request("/just/a/path?q"),
            Request("https://e.com//double"),
            Request("https://e.com", "OPTIONS", target="*"),
            Request("//e.com:443", "CONNECT", target="e.com:443"),
            Request("http://e.com/a?b", target="http://e.com/a?b"),
            Request(http_version="HTTP/1.0"),
        ]
        for req in requests:
            for separator in ("\r\n", "\n"):
                with self.subTest(req=repr(req), separator=separator):
                    text = req.as_str(include_body=True, separator=separator)
                    parsed = Request.from_str(text)
                    self.assertEqual(parsed.as_str(include_body=True, separator=separator), text)
                    self.assertEqual(parsed.method, req.method)
                    self.assertEqual(parsed.body, req.body)

    def test_path_starting_with_two_slashes_without_host(self) -> None:
        """
        Test that a path with an empty first segment is not taken for a
        host when there is no Host header.
        """
        req = Request.from_str("GET //a/b HTTP/1.1")
        self.assertFalse(req.url.hostname)
        self.assertEqual(req.url.path, "//a/b")
        self.assertEqual(req.as_str(), "GET //a/b HTTP/1.1")

    def test_other_target_forms_build_a_url(self) -> None:
        """
        Test that the URL is filled in as far as the text allows for the
        targets that are kept explicitly.
        """
        req = Request.from_str("OPTIONS * HTTP/1.1\nHost: e.com")
        self.assertEqual((req.target, req.url.as_str()), ("*", "//e.com"))
        req = Request.from_str("OPTIONS * HTTP/1.1")
        self.assertEqual((req.target, req.url.as_str()), ("*", ""))
        req = Request.from_str("CONNECT e.com:443 HTTP/1.1", scheme="https")
        self.assertEqual((req.target, req.url.hostname, req.url.port), ("e.com:443", "e.com", 443))
        req = Request.from_str("GET http://e.com/a?b HTTP/1.1\nHost: proxy", scheme="https")
        self.assertEqual((req.target, req.url.as_str()), ("http://e.com/a?b", "http://e.com/a?b"))

    def test_target_the_url_would_respell_is_kept(self) -> None:
        """
        Test that a target the Url does not render the way it is written
        is kept explicitly: the request line comes out as it came in.
        """
        text = "GET /a%zzb HTTP/1.1\r\nHost: e.com"
        req = Request.from_str(text)
        self.assertEqual(req.target, "/a%zzb")
        self.assertEqual(req.as_str(), text)

    def test_leading_empty_lines_are_skipped(self) -> None:
        """
        Test that empty lines ahead of the request line are ignored.
        """
        self.assertEqual(Request.from_str("\r\n\r\nGET / HTTP/1.1\r\nHost: a").method, "GET")

    def test_invalid_text(self) -> None:
        """
        Test that a request line with too few or too many parts, and an
        invalid header line, raise a ValueError.
        """
        for text in (
            "",
            "GET /",
            "GET  / HTTP/1.1",
            "GET / HTTP/1.1 extra",
            "GET / HTTP/1.1\nno colon here",
            "GET / HTTP/1.1\nName : value",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    Request.from_str(text)

    def test_subclass(self) -> None:
        """
        Test that the classmethod returns an instance of the class it is
        called on.
        """

        class _Traced(Request):
            pass

        self.assertIs(type(_Traced.from_str("GET / HTTP/1.1")), _Traced)


class RequestCloseTestCase(unittest.TestCase):
    """
    tests for close(), aclose() and the context managers of
    :py:class:`action0.req.request.Request`
    """

    def test_close_is_a_noop_for_plain_bodies(self) -> None:
        """
        Test that requests whose body holds nothing can be closed any
        number of times.
        """
        for body in (None, b"bytes", "text", BytesBody(b"x")):
            with self.subTest(body=type(body).__name__):
                req = Request("https://a/", body=body)
                req.close()
                req.close()
                asyncio.run(req.aclose())

    def test_close_releases_a_streamed_body(self) -> None:
        """
        Test that close() runs the callback of a body that was never
        sent, and that the with block does the same.
        """
        released: list[bool] = []
        req = Request(
            "https://a/",
            "PUT",
            body=IterableBody(iter([b"x"]), close=lambda: released.append(True)),
        )
        req.close()
        req.close()
        self.assertEqual(released, [True])

        body = IterableBody(iter([b"x"]), close=lambda: released.append(True))
        with Request("https://a/", "PUT", body=body) as entered:
            self.assertIsInstance(entered, Request)
        self.assertEqual(released, [True, True])

    def test_close_refuses_an_async_only_body(self) -> None:
        """
        Test that close() does not silently skip a body only aclose()
        can release, and that aclose() and ``async with`` release it.
        """
        released: list[bool] = []

        async def source() -> AsyncIterator[bytes]:
            yield b"x"  # pragma: no cover

        async def release() -> None:
            released.append(True)

        async def main() -> None:
            req = Request("https://a/", "PUT", body=AsyncIterableBody(source(), aclose=release))
            with self.assertRaises(RuntimeError):
                req.close()
            self.assertEqual(released, [])
            await req.aclose()
            self.assertEqual(released, [True])

            body = AsyncIterableBody(source(), aclose=release)
            async with Request("https://a/", "PUT", body=body) as entered:
                self.assertIsInstance(entered, Request)
            self.assertEqual(released, [True, True])

        asyncio.run(main())

    def test_context_manager_does_not_replace_the_error(self) -> None:
        """
        Test that a failure to close is attached as a note to the
        exception the block was left by — sync and async.
        """

        def failing() -> None:
            raise OSError("cannot close")

        with self.assertRaises(KeyError) as raised:
            with Request("https://a/", body=IterableBody([b"x"], close=failing)):
                raise KeyError("boom")
        self.assertIn("closing the request failed as well", raised.exception.__notes__[0])

        async def main() -> None:
            async with Request("https://a/", body=IterableBody([b"x"], close=failing)):
                raise KeyError("boom")

        with self.assertRaises(KeyError) as raised:
            asyncio.run(main())
        self.assertIn("closing the request failed as well", raised.exception.__notes__[0])

        # without an exception in flight the failure is the error
        with self.assertRaises(OSError):
            with Request("https://a/", body=IterableBody([b"x"], close=failing)):
                pass


class RequestAsyncBodyTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for the asynchronous body accessors of
    :py:class:`action0.req.request.Request`
    """

    @staticmethod
    async def _source() -> AsyncIterator[bytes]:
        yield "grü".encode("latin-1")
        yield "ße".encode("latin-1")

    async def test_plain_bodies(self) -> None:
        """
        Test that bytes, str and no body come out as with the
        synchronous accessors.
        """
        latin = {"Content-Type": "text/plain; charset=latin-1"}
        self.assertIsNone(await Request().abody_bytes())
        self.assertIsNone(await Request().abody_str())
        self.assertEqual(await Request(body="ä", headers=latin).abody_bytes(), b"\xe4")
        self.assertEqual(await Request(body=b"\xe4", headers=latin).abody_str(), "ä")

    async def test_async_stream(self) -> None:
        """
        Test that a body which can only be read asynchronously is read,
        and decoded with the Content-Type charset.
        """
        latin = {"Content-Type": "text/plain; charset=latin-1"}
        req = Request(body=AsyncIterableBody(self._source()), headers=latin)
        self.assertEqual(await req.abody_bytes(), "grüße".encode("latin-1"))
        req = Request(body=AsyncIterableBody(self._source()), headers=latin)
        self.assertEqual(await req.abody_str(), "grüße")
        with self.assertRaisesRegex(RuntimeError, "has been read already"):
            await req.abody_bytes()

    async def test_sync_producers(self) -> None:
        """
        Test that the producers readable both ways work as well.
        """
        self.assertEqual(await Request(body=BytesBody(b"abc")).abody_bytes(), b"abc")
        self.assertEqual(await Request(body=IterableBody([b"a", b"b"])).abody_str(), "ab")


class RequestReprFragmentTestCase(unittest.TestCase):
    """
    tests for the redaction of secrets in the fragment of the URL by
    :py:meth:`action0.req.request.Request.__repr__`
    """

    def test_tokens_in_the_fragment_are_redacted(self) -> None:
        """
        Test the shape the OAuth implicit flow returns its tokens in: a
        fragment written like a query.
        """
        req = Request("https://app.example/cb#access_token=SECRET&token_type=bearer&id_token=X")
        self.assertEqual(
            repr(req),
            "Request(GET https://app.example/cb#access_token=***&token_type=bearer&id_token=***)",
        )

    def test_query_of_a_client_side_route_is_redacted(self) -> None:
        """
        Test a fragment that is a route with a query of its own, next to
        a query of the URL.
        """
        req = Request("https://app.example/?key=K&page=2#/callback?token=T&state=s")
        self.assertEqual(
            repr(req),
            "Request(GET https://app.example/?key=***&page=2#/callback?token=***&state=s)",
        )

    def test_only_the_repr_redacts_the_fragment(self) -> None:
        """
        Test that the URL itself keeps the value.
        """
        req = Request("https://app.example/cb#access_token=SECRET")
        self.assertEqual(req.url.as_str(), "https://app.example/cb#access_token=SECRET")

    def test_instance_set_applies_to_the_fragment(self) -> None:
        """
        Test that the fragment is redacted by the request's own set of
        names.
        """
        req = Request("https://a/p#session=abc&token=t")
        req.secret_params = frozenset({"session"})
        self.assertEqual(repr(req), "Request(GET https://a/p#session=***&token=t)")


class RequestCopyInstanceAttributesTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.request.Request.copy` taking along
    what has been set on the instance
    """

    def test_copy_keeps_the_secret_params_of_the_instance(self) -> None:
        """
        Test that a copy redacts what the original redacts — a copy made
        for a retry or a redirect must not log the secret in clear.
        """
        req = Request("https://h/?session=abc")
        req.secret_params = req.secret_params | {"session"}
        self.assertEqual(repr(req), "Request(GET https://h/?session=***)")
        self.assertEqual(repr(req.copy()), "Request(GET https://h/?session=***)")
        self.assertEqual(repr(req.copy(method="POST")), "Request(POST https://h/?session=***)")
        self.assertEqual(repr(req.copy().copy()), "Request(GET https://h/?session=***)")

    def test_class_default_is_untouched(self) -> None:
        """
        Test that a copy of a request without a set of its own has none
        either, so a later change of the class attribute reaches it.
        """
        duplicate = Request("https://h/").copy()
        self.assertNotIn("secret_params", vars(duplicate))
        self.assertIs(duplicate.secret_params, Request.secret_params)

    def test_other_instance_attributes_go_along(self) -> None:
        """
        Test that any attribute set on the instance is taken over
        (shared, not copied), and that the parts of the request are
        still the copy's own.
        """
        req = Request("https://h/a", headers={"A": "1"}, meta={"k": "v"})
        marker = object()
        req.__dict__["note"] = marker
        duplicate = req.copy(method="PUT")
        self.assertIs(duplicate.__dict__["note"], marker)
        self.assertEqual(duplicate.method, "PUT")
        self.assertIsNot(duplicate.url, req.url)
        self.assertIsNot(duplicate.headers, req.headers)
        self.assertIsNot(duplicate.meta, req.meta)
        self.assertEqual(duplicate, req.copy(method="PUT"))


class RequestStartLineSafetyTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.request.Request.as_str` refusing a
    method, target or version that would not stay one token of the
    request line
    """

    def test_whitespace_in_the_method_is_refused(self) -> None:
        """
        Test that a method cannot carry a target and a version of its
        own into the request line.
        """
        for method in ("GET /admin HTTP/1.1\tX", "GET /admin", "GET\t", "G\xa0ET", "G\x0bET"):
            with self.subTest(method=method):
                with self.assertRaisesRegex(ValueError, "method contains whitespace"):
                    Request("https://h/p", method).as_str()

    def test_whitespace_in_the_target_is_refused(self) -> None:
        """
        Test that an explicit target with a space — which would read as
        a target and a version — raises.
        """
        for target in ("/a b", "/a b HTTP/1.0", "/a\tb", " /a", "/a "):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "request target contains whitespace"):
                    Request("https://h/p", target=target).as_str()

    def test_whitespace_in_the_version_is_refused(self) -> None:
        """
        Test the same for the HTTP version.
        """
        with self.assertRaisesRegex(ValueError, "HTTP version contains whitespace"):
            Request("https://h/p", http_version="HTTP/1.1 X").as_str()

    def test_empty_parts_are_refused(self) -> None:
        """
        Test that an empty method, target or version — which would leave
        two parts on the line — raises.
        """
        with self.assertRaisesRegex(ValueError, "method must not be empty"):
            Request("https://h/p", "").as_str()
        with self.assertRaisesRegex(ValueError, "request target must not be empty"):
            Request("https://h/p", target="").as_str()
        with self.assertRaisesRegex(ValueError, "HTTP version must not be empty"):
            Request("https://h/p", http_version="").as_str()

    def test_what_is_rendered_can_be_parsed(self) -> None:
        """
        Test that the checks leave the regular request lines alone,
        whatever their target form.
        """
        for req in (
            Request("https://h/p?q=a%20b"),
            Request("https://h", "OPTIONS", target="*"),
            Request("//h:443", "CONNECT", target="h:443"),
            Request("http://h/x", target="http://h/x", http_version="HTTP/1.0"),
        ):
            with self.subTest(req=req):
                self.assertEqual(Request.from_str(req.as_str()).as_str(), req.as_str())


class RequestAttributeSettersTestCase(unittest.TestCase):
    """
    tests for assigning to :py:attr:`action0.req.request.Request.url`
    and :py:attr:`~action0.req.request.Request.headers`
    """

    def test_headers_mapping_is_converted(self) -> None:
        """
        Test that a dict assigned as the headers becomes a Headers
        instance: lookups stay case-insensitive, the charset is found
        and the rendering works.
        """
        req = Request("https://h/", "POST", body=b"\xe9")
        req.headers = {"content-type": "text/plain; charset=latin-1"}
        self.assertIsInstance(req.headers, Headers)
        self.assertEqual(req.headers["Content-Type"], "text/plain; charset=latin-1")
        self.assertEqual(req.body_str(), "é")
        self.assertEqual(
            req.as_str(), "POST / HTTP/1.1\r\nHost: h\r\ncontent-type: text/plain; charset=latin-1"
        )

    def test_other_header_forms_are_converted(self) -> None:
        """
        Test the forms the Headers constructor takes: a list of tuples,
        a raw block, ``None``.
        """
        req = Request("https://h/")
        req.headers = [("A", "1"), ("A", "2")]
        self.assertEqual(req.headers.get_all("a"), ["1", "2"])
        req.headers = "B: 3\r\nC: 4"
        self.assertEqual(dict(req.headers), {"B": "3", "C": "4"})
        req.headers = None
        self.assertEqual(len(req.headers), 0)
        self.assertIsInstance(req.headers, Headers)

    def test_headers_instance_is_taken_as_it_is(self) -> None:
        """
        Test that assigning a Headers instance does not copy it (only
        the constructor does), and keeps its class.
        """

        class Quiet(Headers):
            pass

        headers = Quiet({"A": "1"})
        req = Request("https://h/")
        req.headers = headers
        self.assertIs(req.headers, headers)

    def test_invalid_headers_are_refused(self) -> None:
        """
        Test that what cannot be headers raises on assignment, and
        leaves the headers as they were.
        """
        req = Request("https://h/", headers={"A": "1"})
        for value in (5, {"X": None}, {"X ": "1"}):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    req.headers = cast("Any", value)
        self.assertEqual(dict(req.headers), {"A": "1"})

    def test_url_string_is_parsed(self) -> None:
        """
        Test that a string assigned as the URL becomes a Url.
        """
        req = Request("https://h/a")
        req.url = "https://other.example/b?c=1"
        self.assertIsInstance(req.url, Url)
        self.assertEqual(req.as_str(), "GET /b?c=1 HTTP/1.1\r\nHost: other.example")

    def test_url_instance_is_taken_as_it_is(self) -> None:
        """
        Test that assigning a Url does not copy it, and that ``None``
        gives an empty URL as in the constructor.
        """
        url = Url("https://h/a")
        req = Request()
        req.url = url
        self.assertIs(req.url, url)
        req.url = None
        self.assertEqual(req.url.as_str(), "")

    def test_what_is_no_url_is_refused(self) -> None:
        """
        Test that anything else raises, and leaves the URL as it was.
        """
        req = Request("https://h/a")
        for value in (5, b"https://h/", ["https://h/"]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TypeError, "the URL must be a Url or a str"):
                    req.url = cast("Any", value)
        self.assertEqual(req.url.as_str(), "https://h/a")

    def test_pickle_keeps_the_attributes(self) -> None:
        """
        Test that a request survives pickling with its URL and headers
        — they live in the instance dict under their public names, where
        a pickle from before they were properties has them too.
        """
        req = Request("https://h/a?b=1", "POST", headers={"A": "1"}, body=b"x")
        self.assertEqual(set(vars(req)) >= {"url", "headers", "method"}, True)
        restored = pickle.loads(pickle.dumps(req))
        self.assertEqual(restored, req)
        self.assertEqual(restored.headers["a"], "1")
        self.assertEqual(restored.url.as_str(), "https://h/a?b=1")


class RequestFromStrHostTestCase(unittest.TestCase):
    """
    tests for what :py:meth:`action0.req.request.Request.from_str`
    makes of the ``Host`` header
    """

    def test_host_with_more_than_a_host_is_refused(self) -> None:
        """
        Test that a Host value cannot become the path, the query, the
        fragment or the userinfo of the URL.
        """
        for host in (
            "e.com/evil?x",
            "e.com?x",
            "e.com#x",
            "trusted.com@evil.com",
            "user:pw@evil.com",
            "e.com\\evil",
            "a.com b.com",
            "a.com\tb.com",
        ):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ValueError, "invalid Host header"):
                    Request.from_str(f"GET /a HTTP/1.1\nHost: {host}\n\n", scheme="https")

    def test_more_than_one_host_is_refused(self) -> None:
        """
        Test that two Host lines raise instead of the last one winning.
        """
        with self.assertRaisesRegex(ValueError, "more than one Host header"):
            Request.from_str("GET /a HTTP/1.1\nHost: a.com\nhost: b.com\n\n")

    def test_regular_hosts_are_taken(self) -> None:
        """
        Test the values a Host header does have: a name, with a port, an
        IPv4 and an IPv6 address, and an empty one.
        """
        for host, expected in (
            ("example.com", "//example.com/a"),
            ("example.com:8443", "//example.com:8443/a"),
            ("127.0.0.1:80", "//127.0.0.1:80/a"),
            ("[::1]:8080", "//[::1]:8080/a"),
            ("", "/a"),
        ):
            with self.subTest(host=host):
                req = Request.from_str(f"GET /a HTTP/1.1\nHost: {host}\n\n")
                self.assertEqual(req.url.as_str(), expected)
                self.assertIsNone(req.target)

    def test_lookalike_of_host_is_not_the_host(self) -> None:
        """
        Test that a field whose name merely casefolds to "host" neither
        provides the host of the URL nor stands in for the Host header
        when the request is rendered.
        """
        req = Request.from_str("GET /a HTTP/1.1\nHoſt: evil.com\n\n")
        self.assertEqual(req.url.as_str(), "/a")
        req = Request("https://example.com/", headers={"Hoſt": "evil"})
        self.assertEqual(req.as_str(), "GET / HTTP/1.1\r\nHost: example.com\r\nHoſt: evil")

    def test_what_a_request_always_is_differs_from_the_text(self) -> None:
        """
        Test the documented exceptions from the round trip: the method
        comes back in uppercase, and a target naming the host comes with
        the Host header derived from it.
        """
        self.assertEqual(Request.from_str("get /a HTTP/1.1").as_str(), "GET /a HTTP/1.1")
        self.assertEqual(
            Request.from_str("GET http://example.com/x HTTP/1.0").as_str(separator="\n"),
            "GET http://example.com/x HTTP/1.0\nHost: example.com",
        )
        self.assertEqual(
            Request.from_str("CONNECT example.com:443 HTTP/1.1").as_str(separator="\n"),
            "CONNECT example.com:443 HTTP/1.1\nHost: example.com:443",
        )


class RequestBodyTypeTestCase(unittest.TestCase):
    """
    tests for a body of a type that is bytes-like but not a body:
    ``bytearray`` and ``memoryview``
    """

    def test_accessors_say_what_is_wrong(self) -> None:
        """
        Test that every accessor raises a TypeError naming the type,
        instead of an AttributeError about a method the object lacks.
        """
        for body in (bytearray(b"x"), memoryview(b"x")):
            req = Request("http://x/", "POST", body=cast("Any", body))
            for access in (
                req.body_bytes,
                req.body_str,
                req.body_producer,
                lambda req=req: req.as_str(include_body=True),
                lambda req=req: asyncio.run(req.abody_bytes()),
                lambda req=req: asyncio.run(req.abody_str()),
            ):
                with self.subTest(body=type(body).__name__, access=access):
                    with self.assertRaisesRegex(TypeError, "convert it with bytes"):
                        access()

    def test_rendering_without_the_body_works(self) -> None:
        """
        Test that the body is only looked at where it is asked for.
        """
        req = Request("http://x/", "POST", body=cast("Any", bytearray(b"x")))
        self.assertEqual(req.as_str(), "POST / HTTP/1.1\r\nHost: x")

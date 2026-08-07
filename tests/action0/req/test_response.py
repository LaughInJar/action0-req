import unittest

from action0.req import BytesBody
from action0.req import Headers
from action0.req import Request
from action0.req import Response
from action0.req import Status


class ResponseInitTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.response.Response.__init__`
    """

    def test_defaults(self) -> None:
        """
        Test the empty response: 200, empty headers, no body, no request.
        """
        resp = Response()
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(resp.headers), 0)
        self.assertIsNone(resp.body)
        self.assertIsNone(resp.reason)
        self.assertEqual(resp.http_version, "HTTP/1.1")
        self.assertIsNone(resp.request)

    def test_status_forms(self) -> None:
        """
        Test that Status members and plain (even unregistered) ints work.
        """
        self.assertEqual(Response(Status.NOT_FOUND).status, 404)
        self.assertEqual(Response(599).status, 599)

    def test_headers_coerced_and_copied(self) -> None:
        """
        Test that headers accept any Headers input form and instances are
        copied.
        """
        resp = Response(headers={"Content-Type": "text/plain"})
        self.assertEqual(resp.headers["content-type"], "text/plain")

        headers = Headers({"Content-Type": "text/plain"})
        resp = Response(headers=headers)
        resp.headers.add("X-More", "1")
        self.assertNotIn("X-More", headers)

    def test_request_reference_is_shared(self) -> None:
        """
        Test that the request back-reference is stored as-is.
        """
        req = Request("https://api.example.com/items")
        resp = Response(200, request=req)
        self.assertIs(resp.request, req)


class ResponsePhraseTestCase(unittest.TestCase):
    """
    tests for :py:attr:`action0.req.response.Response.phrase` and the
    category properties
    """

    def test_phrase_from_registry(self) -> None:
        """
        Test the fallback to the Status registry phrase.
        """
        self.assertEqual(Response(404).phrase, "Not Found")
        self.assertEqual(Response(Status.IM_A_TEAPOT).phrase, "I'm a Teapot")

    def test_phrase_explicit_reason_wins(self) -> None:
        """
        Test that a reason sent by the server beats the registry.
        """
        self.assertEqual(Response(404, reason="Nope").phrase, "Nope")
        self.assertEqual(Response(404, reason="").phrase, "")

    def test_phrase_unknown_code(self) -> None:
        """
        Test that unregistered codes have an empty phrase.
        """
        self.assertEqual(Response(599).phrase, "")

    def test_categories(self) -> None:
        """
        Test the category properties, including unregistered codes.
        """
        self.assertTrue(Response(100).is_informational)
        self.assertTrue(Response(204).is_success)
        self.assertTrue(Response(308).is_redirection)
        self.assertTrue(Response(404).is_client_error)
        self.assertTrue(Response(599).is_server_error)
        self.assertFalse(Response(404).is_success)


class ResponseBodyTestCase(unittest.TestCase):
    """
    tests for the body accessors of :py:class:`action0.req.response.Response`
    """

    def test_no_body(self) -> None:
        """
        Test that all accessors return None without a body.
        """
        resp = Response()
        self.assertIsNone(resp.body_bytes())
        self.assertIsNone(resp.body_str())
        self.assertIsNone(resp.body_producer())

    def test_accessors_convert_between_forms(self) -> None:
        """
        Test that each accessor works regardless of how the body was set.
        """
        resp = Response(body="hällo")
        self.assertEqual(resp.body_str(), "hällo")
        self.assertEqual(resp.body_bytes(), "hällo".encode())

        resp = Response(body=b"hello")
        self.assertEqual(resp.body_str(), "hello")
        producer = resp.body_producer()
        assert producer is not None
        self.assertEqual(producer.as_bytes(), b"hello")

        producer = BytesBody(b"data")
        resp = Response(body=producer)
        self.assertIs(resp.body_producer(), producer)
        self.assertEqual(resp.body_bytes(), b"data")

    def test_charset_from_content_type(self) -> None:
        """
        Test that decoding honors the Content-Type charset.
        """
        resp = Response(
            body="hällo".encode("latin-1"),
            headers={"Content-Type": "text/plain; charset=latin-1"},
        )
        self.assertEqual(resp.body_str(), "hällo")


class ResponseAsStrTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.response.Response.as_str`
    """

    def test_status_line_and_headers(self) -> None:
        """
        Test the status line with the registry phrase and the header lines.
        """
        resp = Response(404, headers={"Content-Length": 0})
        self.assertEqual(resp.as_str(), "HTTP/1.1 404 Not Found\r\nContent-Length: 0")

    def test_status_line_unknown_code(self) -> None:
        """
        Test that an unregistered code renders without a trailing space.
        """
        self.assertEqual(Response(599).as_str(), "HTTP/1.1 599")

    def test_include_body(self) -> None:
        """
        Test that include_body appends a blank line and the text body.
        """
        resp = Response(200, headers={"Content-Type": "text/plain"}, body="hi")
        self.assertEqual(
            resp.as_str(include_body=True, separator="\n"),
            "HTTP/1.1 200 OK\nContent-Type: text/plain\n\nhi",
        )

    def test_include_body_does_not_consume_producer(self) -> None:
        """
        Test that a BodyProducer body is shown as a placeholder only.
        """
        resp = Response(200, body=BytesBody(b"data"))
        rendered = resp.as_str(include_body=True)
        self.assertIn("<BytesBody>", rendered)
        self.assertNotIn("data", rendered)


class ResponseCopyEqReprTestCase(unittest.TestCase):
    """
    tests for copy(), __eq__ and __repr__ of
    :py:class:`action0.req.response.Response`
    """

    def test_copy_is_independent(self) -> None:
        """
        Test that the copy has its own Headers.
        """
        resp = Response(200, headers={"Content-Type": "text/plain"})
        clone = resp.copy()
        self.assertEqual(clone, resp)
        clone.headers["Content-Type"] = "text/html"
        self.assertEqual(resp.headers["Content-Type"], "text/plain")

    def test_copy_with_overrides(self) -> None:
        """
        Test that constructor keywords override the copied attributes.
        """
        resp = Response(200, body="ok")
        clone = resp.copy(status=404, reason="Nope")
        self.assertEqual(clone.status, 404)
        self.assertEqual(clone.reason, "Nope")
        self.assertEqual(clone.body, "ok")
        self.assertEqual(resp.status, 200)

    def test_eq(self) -> None:
        """
        Test equality across the parts; the request reference is metadata
        and ignored.
        """
        self.assertEqual(
            Response(404, headers={"A": "1"}, body=b"x"),
            Response(Status.NOT_FOUND, headers={"a": "1"}, body=b"x"),
        )
        self.assertNotEqual(Response(404), Response(404, reason="Nope"))
        self.assertNotEqual(Response(body=b"x"), Response(body="x"))
        self.assertNotEqual(Response(), "HTTP/1.1 200 OK")

        req = Request("https://api.example.com/")
        self.assertEqual(Response(200, request=req), Response(200))

    def test_repr(self) -> None:
        """
        Test that repr()/str() show status and phrase but no header values.
        """
        resp = Response(404, headers={"Set-Cookie": "secret=1"})
        self.assertEqual(repr(resp), "Response(404 Not Found)")
        self.assertEqual(str(resp), repr(resp))
        self.assertNotIn("secret", repr(resp))
        self.assertEqual(repr(Response(599)), "Response(599)")
        self.assertEqual(repr(Response(404, reason="Nope")), "Response(404 Nope)")


class MetaTestCase(unittest.TestCase):
    """
    tests for the :py:attr:`Response.meta` application metadata
    """

    def test_defaults_to_an_empty_dict(self) -> None:
        """
        Test that every response has its own empty meta dict.
        """
        response = Response(200)
        self.assertEqual(response.meta, {})
        response.meta["my-lib.key"] = "value"
        self.assertEqual(Response(200).meta, {})

    def test_constructor_copies_the_given_mapping(self) -> None:
        """
        Test that later changes to the source mapping don't leak in.
        """
        source = {"my-lib.key": "value"}
        response = Response(200, meta=source)
        source["my-lib.key"] = "changed"
        self.assertEqual(response.meta, {"my-lib.key": "value"})

    def test_copy_gets_its_own_meta_dict(self) -> None:
        """
        Test that copies carry the entries but not the dict identity.
        """
        response = Response(200, meta={"my-lib.key": "value"})
        copied = response.copy()
        copied.meta["my-lib.key"] = "changed"
        self.assertEqual(response.meta, {"my-lib.key": "value"})
        overridden = response.copy(meta={"other": 1})
        self.assertEqual(overridden.meta, {"other": 1})

    def test_meta_is_not_part_of_equality(self) -> None:
        """
        Test that meta is metadata, like the docstring promises.
        """
        self.assertEqual(Response(200), Response(200, meta={"my-lib.key": "value"}))

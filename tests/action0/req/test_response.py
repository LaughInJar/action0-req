import asyncio
import gzip
import io
import os
import pickle
import threading
import unittest
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from action0.req import AsyncIterableBody
from action0.req import BytesBody
from action0.req import FileBody
from action0.req import Headers
from action0.req import IterableBody
from action0.req import Request
from action0.req import Response
from action0.req import SpooledBody
from action0.req import Status
from action0.req import body as body_module
from action0.req import response as response_module


class _Stream:
    """
    A minimal third-party producer streaming two chunks; the tests add
    the ``close()`` or ``aclose()`` they are about.
    """

    def content_length(self) -> None:
        return None

    def chunks(self) -> Iterator[bytes]:
        yield b"chunk1"
        yield b"chunk2"

    async def achunks(self) -> AsyncIterator[bytes]:
        yield b"chunk1chunk2"  # pragma: no cover

    def as_bytes(self) -> bytes:
        return b"chunk1chunk2"


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


class ResponseBodyFileTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.response.Response.body_file`
    """

    def test_no_body(self) -> None:
        """
        Test that a response without a body has no file either.
        """
        self.assertIsNone(Response(204).body_file())

    def test_in_memory_bodies(self) -> None:
        """
        Test that bytes, str (encoded with the charset) and BytesBody
        yield a seekable in-memory file — a new one per call.
        """
        latin = {"Content-Type": "text/plain; charset=latin-1"}
        cases = [
            (Response(200, body=b"data"), b"data"),
            (Response(200, headers=latin, body="né"), b"n\xe9"),
            (Response(200, body=BytesBody(b"data")), b"data"),
        ]
        for resp, expected in cases:
            with self.subTest(body=resp.body):
                file = resp.body_file()
                assert file is not None
                self.assertTrue(file.seekable())
                self.assertEqual(file.read(), expected)
                self.assertIsNot(resp.body_file(), file)
                # reading one file to its end does not affect the next
                other = resp.body_file()
                assert other is not None
                self.assertEqual(other.read(), expected)

    def test_spooled_body_hands_out_views(self) -> None:
        """
        Test that a spooled body is not copied, but read through views:
        a new one per call, starting at the beginning, and closing one
        leaves the body and the other views alone.
        """
        spool = SpooledBody(max_memory=0)
        spool.write(b"data")
        with Response(200, body=spool) as resp:
            file = resp.body_file()
            assert file is not None
            self.assertEqual(file.read(2), b"da")
            again = resp.body_file()
            assert again is not None
            self.assertIsNot(again, file)
            again.close()
            self.assertEqual(file.read(), b"ta")
            self.assertIs(resp.body, spool)
            self.assertFalse(spool.closed)
        self.assertTrue(spool.closed)

    def test_streamed_body_is_spooled_and_replaced(self) -> None:
        """
        Test that a streamed body is read to its end into a spool that
        replaces it: the stream is released, the response stays readable,
        and closing the response discards the spool.
        """
        released: list[bool] = []
        stream = IterableBody(iter([b"chunk1", b"chunk2"]), close=lambda: released.append(True))
        resp = Response(200, body=stream)

        file = resp.body_file()
        assert file is not None
        self.assertEqual(file.read(), b"chunk1chunk2")
        self.assertEqual(released, [True])
        self.assertIsInstance(resp.body, SpooledBody)
        self.assertEqual(resp.body_bytes(), b"chunk1chunk2")
        spool = resp.body
        assert isinstance(spool, SpooledBody)

        resp.close()
        self.assertTrue(spool.closed)

    def test_stream_whose_close_callback_fails(self) -> None:
        """
        Test what happens when a stream's own close callback fails at
        the end of the stream: the error passes through and nothing is
        kept — it cannot be told from a stream breaking off.
        """

        def release() -> None:
            raise ConnectionError("pool gone")

        stream = IterableBody(iter([b"all ", b"data"]), close=release)
        resp = Response(200, body=stream)
        with self.assertRaises(ConnectionError):
            resp.body_file()
        self.assertIs(resp.body, stream)

    def test_file_body_over_a_path_is_opened_not_copied(self) -> None:
        """
        Test that the file behind a path-based FileBody is opened once
        and handed out as it is: no spool, the same file (rewound) on
        every call, the response still readable, and close() closing the
        file.
        """
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cached.bin"
            path.write_bytes(b"abcdef")
            resp = Response(200, body=FileBody(path, chunk_size=4))

            body = resp.body
            file = resp.body_file()
            assert file is not None
            self.assertEqual(file.read(), b"abcdef")
            # the body is not replaced, it keeps the file it has opened
            self.assertIs(resp.body, body)
            self.assertEqual(Path(file.name), path)

            self.assertIs(resp.body_file(), file)
            self.assertEqual(file.tell(), 0)
            producer = resp.body_producer()
            assert producer is not None
            self.assertEqual(list(producer.chunks()), [b"abcd", b"ef"])
            self.assertEqual(producer.content_length(), 6)

            resp.close()
            self.assertTrue(file.closed)
            resp.close()

    def test_closing_the_file_of_a_path_body_does_no_harm(self) -> None:
        """
        Test that the body of a path-based FileBody stays readable when
        the file handed out is closed — the obvious thing to do with a
        file — and that the next call opens a new one, which closing the
        response closes.
        """
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cached.bin"
            path.write_bytes(b"abcdef")
            with Response(200, body=FileBody(path)) as resp:
                file = resp.body_file()
                assert file is not None
                with file:
                    self.assertEqual(file.read(), b"abcdef")
                self.assertEqual(resp.body_bytes(), b"abcdef")

                again = resp.body_file()
                assert again is not None
                self.assertIsNot(again, file)
                self.assertEqual(again.read(), b"abcdef")
            self.assertTrue(again.closed)
            # like any path-based FileBody: closing holds nothing back
            self.assertEqual(resp.body_bytes(), b"abcdef")

    def test_file_body_over_a_seekable_file_object(self) -> None:
        """
        Test that a FileBody wrapping a seekable file object hands out
        that very object, rewound — and that it stays the caller's:
        closing the response does not close it.
        """
        source = io.BytesIO(b"abcdef")
        source.seek(3)
        body = FileBody(source)
        resp = Response(200, body=body)

        file = resp.body_file()
        self.assertIs(file, source)
        self.assertEqual(source.tell(), 0)
        self.assertIs(resp.body, body)

        resp.close()
        self.assertFalse(source.closed)

    def test_file_body_that_is_not_seekable_is_spooled(self) -> None:
        """
        Test that a FileBody over a non-seekable file object is spooled
        like any stream.
        """

        class _Pipe(io.BytesIO):
            def seekable(self) -> bool:
                return False

        resp = Response(200, body=FileBody(_Pipe(b"abcdef")))
        with resp:
            file = resp.body_file()
            assert file is not None
            self.assertEqual(file.read(), b"abcdef")
            self.assertIsInstance(resp.body, SpooledBody)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_file_body_over_a_named_pipe_is_opened_once(self) -> None:
        """
        Test that a path which is not a regular file is spooled, and
        without being opened first just to find out whether it is
        seekable: for a pipe, an extra open and close is not without
        effect on the writer.
        """
        with TemporaryDirectory() as directory:
            fifo = Path(directory) / "pipe"
            os.mkfifo(fifo)

            def feed() -> None:
                with open(fifo, "wb") as pipe:
                    pipe.write(b"abcdef")

            writer = threading.Thread(target=feed, daemon=True)
            writer.start()
            # the view would be opened with this function; reading the
            # body does not use it
            refuse = mock.patch.object(
                body_module, "_open_binary", side_effect=AssertionError("opened for a view")
            )
            with refuse, Response(200, body=FileBody(fifo)) as resp:
                file = resp.body_file()
                assert file is not None
                self.assertEqual(file.read(), b"abcdef")
                self.assertIsInstance(resp.body, SpooledBody)
            writer.join(10)

    def test_file_body_subclass_is_read_through_its_chunks(self) -> None:
        """
        Test that a subclass of FileBody keeps what it overrides: the
        file handed out holds what the body produces, not what is in the
        file on disk.
        """

        class _Upper(FileBody):
            def chunks(self) -> Iterator[bytes]:
                for chunk in super().chunks():
                    yield chunk.upper()

        with TemporaryDirectory() as directory:
            path = Path(directory) / "cached.bin"
            path.write_bytes(b"abcdef")
            with Response(200, body=_Upper(path)) as resp:
                self.assertEqual(resp.body_bytes(), b"ABCDEF")
                file = resp.body_file()
                assert file is not None
                self.assertEqual(file.read(), b"ABCDEF")
                self.assertEqual(resp.body_bytes(), b"ABCDEF")

    def test_replaced_producer_is_closed(self) -> None:
        """
        Test that a producer which does not close itself when read to its
        end is closed once the spool has replaced it — afterwards the
        response has no way to reach it anymore.
        """

        class _Connection:
            closed = 0

            def content_length(self) -> None:
                return None

            def chunks(self) -> Iterator[bytes]:
                yield b"chunk1"
                yield b"chunk2"

            async def achunks(self) -> AsyncIterator[bytes]:
                yield b"chunk1chunk2"  # pragma: no cover

            def as_bytes(self) -> bytes:
                return b"chunk1chunk2"  # pragma: no cover

            def close(self) -> None:
                self.closed += 1

        connection = _Connection()
        with Response(200, body=connection) as resp:
            file = resp.body_file()
            assert file is not None
            self.assertEqual(file.read(), b"chunk1chunk2")
            self.assertIsInstance(resp.body, SpooledBody)
            self.assertEqual(connection.closed, 1)
        self.assertEqual(connection.closed, 1)

    def test_async_only_producer_is_released_by_aclose(self) -> None:
        """
        Test that a producer which can only be closed asynchronously is
        not lost when the spool replaces it: close() refuses as before,
        and aclose() releases the producer (once) and the spool.
        """

        class _AsyncConnection(_Stream):
            released = 0

            async def aclose(self) -> None:
                self.released += 1

        connection = _AsyncConnection()
        resp = Response(200, body=connection)
        file = resp.body_file()
        assert file is not None
        self.assertEqual(file.read(), b"chunk1chunk2")
        self.assertIsInstance(resp.body, SpooledBody)

        spool = resp.body
        assert isinstance(spool, SpooledBody)

        with self.assertRaises(RuntimeError):
            resp.close()
        self.assertFalse(spool.closed)
        self.assertEqual(connection.released, 0)

        asyncio.run(resp.aclose())
        asyncio.run(resp.aclose())
        self.assertEqual(connection.released, 1)
        self.assertTrue(spool.closed)
        resp.close()

    def test_producer_failing_to_close(self) -> None:
        """
        Test that the error of a producer's close() reaches the caller
        with the response in order: it holds the spool, readable and
        closable.
        """

        class _Flaky(_Stream):
            def close(self) -> None:
                raise ConnectionError("reset")

        resp = Response(200, body=_Flaky())
        with self.assertRaises(ConnectionError):
            resp.body_file()
        self.assertIsInstance(resp.body, SpooledBody)
        self.assertEqual(resp.body_bytes(), b"chunk1chunk2")
        resp.close()

    def test_broken_stream_leaves_nothing_behind(self) -> None:
        """
        Test that a stream breaking off raises, keeps the body as it was
        and closes the half-filled spool.
        """

        def broken() -> Iterator[bytes]:
            yield b"chunk1"
            raise ConnectionResetError("gone")

        spools: list[SpooledBody] = []

        class _Recording(SpooledBody):
            """keeps track of the spools the response creates"""

            def __init__(self) -> None:
                super().__init__()
                spools.append(self)

        stream = IterableBody(broken())
        resp = Response(200, body=stream)
        with mock.patch.object(response_module, "SpooledBody", _Recording):
            with self.assertRaises(ConnectionResetError):
                resp.body_file()
        self.assertIs(resp.body, stream)
        self.assertEqual(len(spools), 1)
        self.assertTrue(spools[0].closed)

    def test_file_feeds_gzip(self) -> None:
        """
        Test the motivating use: a decompressor reading from the file.
        """
        resp = Response(200, body=gzip.compress(b"report"))
        self.assertEqual(gzip.GzipFile(fileobj=resp.body_file()).read(), b"report")


class ResponseSaveBodyTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.response.Response.save_body` and
    :py:meth:`~action0.req.response.Response.asave_body`
    """

    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "download.bin"

    def test_body_forms(self) -> None:
        """
        Test that every body form is saved: none (an empty file), bytes,
        str (encoded with the charset) and a producer.
        """
        latin = {"Content-Type": "text/plain; charset=latin-1"}
        cases = [
            (Response(204), b""),
            (Response(200, body=b"data"), b"data"),
            (Response(200, headers=latin, body="né"), b"n\xe9"),
            (Response(200, body=IterableBody([b"da", b"ta"])), b"data"),
        ]
        for resp, expected in cases:
            with self.subTest(body=resp.body):
                self.assertEqual(resp.save_body(self.path), len(expected))
                self.assertEqual(self.path.read_bytes(), expected)

    def test_resumed_download(self) -> None:
        """
        Test the resume flow: the partial file is continued with
        ``append=True`` once status and Content-Range have been checked —
        an error response writes nothing.
        """
        self.path.write_bytes(b"first half, ")
        offset = self.path.stat().st_size

        error = Response(416, body=b"<html>range not satisfiable</html>")
        partial = Response(
            206,
            headers={"Content-Range": f"bytes {offset}-22/23"},
            body=IterableBody(iter([b"second", b" half"])),
        )
        for resp in (error, partial):
            content_range = resp.headers.get("Content-Range", "")
            if resp.status == 206 and content_range.startswith(f"bytes {offset}-"):
                resp.save_body(self.path, append=True)

        self.assertEqual(self.path.read_bytes(), b"first half, second half")

    def test_to_file_object(self) -> None:
        """
        Test that a file object is written at its position and left open.
        """
        buffer = io.BytesIO()
        self.assertEqual(Response(200, body="data").save_body(buffer), 4)
        self.assertFalse(buffer.closed)
        self.assertEqual(buffer.getvalue(), b"data")

    def test_async(self) -> None:
        """
        Test the asynchronous twin with a sync-less async stream, a plain
        body and no body.
        """

        async def source() -> AsyncIterator[bytes]:
            yield b"da"
            yield b"ta"

        async def main() -> list[int]:
            written = [await Response(204).asave_body(self.path)]
            written.append(await Response(200, body="data").asave_body(self.path))
            streamed = Response(200, body=AsyncIterableBody(source()))
            written.append(await streamed.asave_body(self.path, append=True))
            return written

        self.assertEqual(asyncio.run(main()), [0, 4, 4])
        self.assertEqual(self.path.read_bytes(), b"datadata")


class ResponseCloseTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.response.Response.close`,
    :py:meth:`~action0.req.response.Response.aclose` and the context
    manager forms
    """

    def test_close_is_a_noop_for_plain_bodies(self) -> None:
        """
        Test that responses holding nothing can be closed, repeatedly,
        and stay readable.
        """
        for resp in (Response(204), Response(200, body=b"data"), Response(200, body="data")):
            with self.subTest(body=resp.body):
                resp.close()
                resp.close()
                asyncio.run(resp.aclose())
                self.assertEqual(resp.body, resp.copy().body)

    def test_close_releases_a_streamed_body(self) -> None:
        """
        Test that closing a response whose streamed body was never read
        releases the stream — once.
        """
        released: list[bool] = []
        body = IterableBody(iter([b"data"]), close=lambda: released.append(True))
        resp = Response(200, body=body)
        resp.close()
        resp.close()
        self.assertEqual(released, [True])

    def test_close_discards_a_spooled_body(self) -> None:
        """
        Test that closing the response closes its spool.
        """
        spool = SpooledBody(max_memory=0)
        spool.write(b"data")
        Response(200, body=spool).close()
        self.assertTrue(spool.closed)

    def test_copies_share_the_body(self) -> None:
        """
        Test the documented consequence of copy() sharing the body:
        closing one copy closes it for all.
        """
        spool = SpooledBody()
        resp = Response(200, body=spool)
        resp.copy(status=201).close()
        self.assertTrue(spool.closed)
        resp.close()

    def test_close_refuses_an_async_only_body(self) -> None:
        """
        Test that close() does not silently skip a body holding something
        that can only be released asynchronously: an async body with an
        ``aclose`` callback, a producer with ``aclose()`` but no
        ``close()``.
        """
        released: list[bool] = []

        async def source() -> AsyncIterator[bytes]:
            yield b"data"  # pragma: no cover

        async def release() -> None:
            released.append(True)

        class _AsyncOnly(BytesBody):
            async def aclose(self) -> None:
                released.append(True)  # pragma: no cover

        for body in (AsyncIterableBody(source(), aclose=release), _AsyncOnly(b"data")):
            with self.subTest(body=type(body).__name__):
                with self.assertRaises(RuntimeError):
                    Response(200, body=body).close()
        self.assertEqual(released, [])

    def test_close_of_an_async_body_holding_nothing(self) -> None:
        """
        Test that close() is a no-op for an async body with nothing to
        release: one without an ``aclose`` callback, or with a callback
        that has run already.
        """
        released: list[bool] = []

        async def source() -> AsyncIterator[bytes]:
            yield b"data"  # pragma: no cover

        async def release() -> None:
            released.append(True)

        with Response(200, body=AsyncIterableBody(source())) as resp:
            resp.close()

        resp = Response(200, body=AsyncIterableBody(source(), aclose=release))
        asyncio.run(resp.aclose())
        resp.close()
        self.assertEqual(released, [True])

    def test_context_manager_does_not_replace_the_error(self) -> None:
        """
        Test that a ``with`` block left by an exception raises that
        exception also when closing fails — with the failure attached as
        a note — while leaving the block normally does raise the failure.
        That holds for a body refusing to be closed synchronously (the
        built-in and any producer following the convention) as well as
        for a close callback that fails for a reason of its own.
        """

        async def source() -> AsyncIterator[bytes]:
            yield b"data"  # pragma: no cover

        async def release() -> None:
            pass  # pragma: no cover

        def failing() -> None:
            raise OSError("pool gone")

        class _Refusing(BytesBody):
            def close(self) -> None:
                raise RuntimeError("still streaming; use aclose()")

            async def aclose(self) -> None:
                pass  # pragma: no cover

        class _AsyncOnly(BytesBody):
            async def aclose(self) -> None:
                pass  # pragma: no cover

        bodies: tuple[Callable[[], Any], ...] = (
            lambda: AsyncIterableBody(source(), aclose=release),
            lambda: _Refusing(b"data"),
            lambda: _AsyncOnly(b"data"),
            lambda: IterableBody(iter([b"data"]), close=failing),
        )
        for index, body in enumerate(bodies):
            with self.subTest(body=index):
                with self.assertRaises(KeyError) as raised:
                    with Response(200, body=body()):
                        raise KeyError("boom")
                (note,) = raised.exception.__notes__
                self.assertIn("closing the response failed as well", note)

                with self.assertRaises((RuntimeError, OSError)):
                    with Response(200, body=body()):
                        pass

    def test_async_context_manager_does_not_replace_the_error(self) -> None:
        """
        Test the same for ``async with``: the exception the block was
        left by is raised, a failing aclose() is attached to it as a
        note — and raised itself when the block is left normally.
        """

        async def source() -> AsyncIterator[bytes]:
            yield b"data"  # pragma: no cover

        async def failing() -> None:
            raise ConnectionError("pool gone")

        async def main() -> None:
            with self.assertRaises(KeyError) as raised:
                async with Response(200, body=AsyncIterableBody(source(), aclose=failing)):
                    raise KeyError("boom")
            (note,) = raised.exception.__notes__
            self.assertIn("ConnectionError", note)

            with self.assertRaises(ConnectionError):
                async with Response(200, body=AsyncIterableBody(source(), aclose=failing)):
                    pass

        asyncio.run(main())

    def test_context_manager(self) -> None:
        """
        Test that ``with response:`` closes on the way out, also when the
        block raises.
        """
        released: list[bool] = []

        def streamed() -> Response:
            body = IterableBody(iter([b"data"]), close=lambda: released.append(True))
            return Response(200, body=body)

        with streamed() as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(released, [True])

        with self.assertRaises(KeyError):
            with streamed():
                raise KeyError("boom")
        self.assertEqual(released, [True, True])

    def test_aclose_and_async_context_manager(self) -> None:
        """
        Test that aclose() awaits an async body's own aclose(), falls
        back to close() for a synchronous one, and that ``async with``
        uses it.
        """
        released: list[str] = []

        async def source() -> AsyncIterator[bytes]:
            yield b"data"

        async def release() -> None:
            released.append("async")

        async def main() -> None:
            await Response(200, body=AsyncIterableBody(source(), aclose=release)).aclose()

            sync_body = IterableBody(iter([b"data"]), close=lambda: released.append("sync"))
            await Response(200, body=sync_body).aclose()

            spool = SpooledBody(max_memory=0)
            await spool.awrite(b"data")
            async with Response(200, body=spool) as resp:
                self.assertIs(resp.body, spool)
            self.assertTrue(spool.closed)

        asyncio.run(main())
        self.assertEqual(released, ["async", "sync"])


class ResponsePickleTestCase(unittest.TestCase):
    """
    tests for pickling a :py:class:`action0.req.response.Response` — what
    out-of-process stores (caches, queues) rely on
    """

    def test_round_trip(self) -> None:
        """
        Test that a response with an in-memory body survives pickling
        with every part intact: status, reason, multi-value headers,
        body, HTTP version, meta and the request reference.
        """
        request = Request("https://api.example.com/items?page=2", headers={"Accept": "*/*"})
        resp = Response(
            203,
            headers=[("Set-Cookie", "a=1"), ("set-cookie", "b=2"), ("Content-Type", "text/plain")],
            body=b"\x00binary\xff",
            reason="Sort Of",
            http_version="HTTP/2",
            request=request,
            meta={"my-lib.cached": True},
        )
        for protocol in range(2, pickle.HIGHEST_PROTOCOL + 1):
            with self.subTest(protocol=protocol):
                restored = pickle.loads(pickle.dumps(resp, protocol))
                self.assertEqual(restored, resp)
                self.assertEqual(restored.headers.as_lines(), resp.headers.as_lines())
                self.assertEqual(restored.meta, {"my-lib.cached": True})
                self.assertEqual(restored.request, request)
                self.assertEqual(restored.phrase, "Sort Of")

    def test_round_trip_without_request(self) -> None:
        """
        Test the form a cache would store: no request reference, a str
        body.
        """
        resp = Response(200, headers={"Content-Type": "application/json"}, body='{"a": 1}')
        restored = pickle.loads(pickle.dumps(resp))
        self.assertEqual(restored, resp)
        self.assertIsNone(restored.request)

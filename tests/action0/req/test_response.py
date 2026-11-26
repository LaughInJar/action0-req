import asyncio
import contextlib
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
from typing import cast
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


@contextlib.contextmanager
def _recorded_hops() -> Iterator[list[str]]:
    """
    Record the name of every function the response module hands to a
    worker thread, while still running it there.
    """
    hops: list[str] = []
    real = body_module._in_thread

    async def dispatch(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        hops.append(getattr(function, "__name__", repr(function)))
        return await real(function, *args, **kwargs)

    with mock.patch.object(response_module, "_in_thread", dispatch):
        yield hops


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
        Test that an unregistered code — no phrase — still renders the
        space behind the status code: the status line requires it, with
        or without a phrase (RFC 9112, section 4).
        """
        self.assertEqual(Response(599).as_str(), "HTTP/1.1 599 ")
        self.assertEqual(Response(200, reason="").as_str(), "HTTP/1.1 200 ")
        self.assertEqual(
            Response(599, headers={"A": "1"}).as_str(separator="\n"), "HTTP/1.1 599 \nA: 1"
        )
        # the repr is not the wire format
        self.assertEqual(repr(Response(599)), "Response(599)")

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
        Test that the file behind a path-based FileBody is handed out as
        it is: no spool, the body not replaced, a newly opened file with
        a position of its own per call, and close() closing those still
        open.
        """
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cached.bin"
            path.write_bytes(b"abcdef")
            body = FileBody(path, chunk_size=4)
            resp = Response(200, body=body)

            file = resp.body_file()
            assert file is not None
            self.assertEqual(Path(file.name), path)
            self.assertEqual(file.read(4), b"abcd")
            self.assertIs(resp.body, body)

            other = resp.body_file()
            assert other is not None
            self.assertIsNot(other, file)
            self.assertEqual(other.read(), b"abcdef")
            self.assertEqual(file.read(), b"ef")
            self.assertEqual(list(body.chunks()), [b"abcd", b"ef"])

            other.close()
            resp.close()
            self.assertTrue(file.closed)
            resp.close()

    def test_copies_get_files_of_their_own(self) -> None:
        """
        Test that responses sharing a path-based FileBody — copies, a
        cached response served twice — do not share a file position.
        """
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cached.bin"
            path.write_bytes(b"abcdef")
            first = Response(200, body=FileBody(path))
            second = first.copy()
            with first, second:
                one = first.body_file()
                assert one is not None
                self.assertEqual(one.read(4), b"abcd")
                two = second.body_file()
                assert two is not None
                self.assertEqual(two.read(), b"abcdef")
                self.assertEqual(one.read(), b"ef")

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

    def test_file_body_subclass_reading_as_usual_is_not_copied(self) -> None:
        """
        Test that a subclass of FileBody which does not change how the
        file is read — it only adds something of its own — hands out its
        file like the base class, without a copy.
        """

        class _Named(FileBody):
            filename = "report.bin"

        with TemporaryDirectory() as directory:
            path = Path(directory) / "cached.bin"
            path.write_bytes(b"abcdef")
            body = _Named(path)
            with Response(200, body=body) as resp:
                file = resp.body_file()
                assert file is not None
                self.assertEqual(Path(file.name), path)
                self.assertIs(resp.body, body)

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

            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
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


class ResponseStatusPropertyTestCase(unittest.TestCase):
    """
    tests for :py:attr:`action0.req.response.Response.status` as a
    property
    """

    def test_non_int_status_is_refused(self) -> None:
        """
        Test that a status that is not an int raises right away — in
        the constructor, in an assignment and in copy() — instead of
        failing later in a category property.
        """
        statuses: list[Any] = ["404", 404.0, None, b"404"]
        for status in statuses:
            with self.subTest(status=status):
                with self.assertRaises(TypeError):
                    Response(status)
                with self.assertRaises(TypeError):
                    Response(200).copy(status=status)
        resp = Response(200)
        with self.assertRaises(TypeError):
            resp.status = cast("Any", "404")
        self.assertEqual(resp.status, 200)

    def test_assignment(self) -> None:
        """
        Test that the status stays a plain mutable attribute to its
        users.
        """
        resp = Response(200)
        resp.status = Status.NOT_FOUND
        self.assertEqual((resp.status, resp.phrase), (404, "Not Found"))
        resp.status = 599
        self.assertTrue(resp.is_server_error)

    def test_pickle_from_before_the_property(self) -> None:
        """
        Test that the status is kept where a plain attribute lives, so
        a pickle written by an earlier release still loads.
        """
        resp = Response(404, body=b"x")
        self.assertEqual(vars(resp)["status"], 404)
        old = Response.__new__(Response)
        old.__dict__.update(vars(resp))
        self.assertEqual(old.status, 404)
        self.assertEqual(pickle.loads(pickle.dumps(resp)), resp)


class ResponseRenderingSafetyTestCase(unittest.TestCase):
    """
    tests for what :py:meth:`action0.req.response.Response.as_str`
    refuses to render, and for what it renders whatever the body is
    """

    def test_line_breaks_in_the_status_line_are_refused(self) -> None:
        """
        Test that neither the reason nor the version can start a line
        of its own.
        """
        for resp in (
            Response(200, reason="OK\r\nX: 1"),
            Response(200, reason="OK\nX: 1"),
            Response(200, http_version="HTTP/1.1\r\nX: 1"),
            Response(200, reason="a\x00b"),
        ):
            with self.subTest(reason=resp.reason):
                with self.assertRaises(ValueError):
                    resp.as_str()

    def test_binary_body_is_rendered_with_replacements(self) -> None:
        """
        Test that a compressed body does not make the rendering fail.
        """
        resp = Response(200, body=gzip.compress(b"report"))
        rendered = resp.as_str(include_body=True)
        self.assertTrue(rendered.startswith("HTTP/1.1 200 OK\r\n\r\n\x1f�"))
        with self.assertRaises(UnicodeDecodeError):
            resp.body_str()

    def test_unknown_charset_is_rendered_as_utf8(self) -> None:
        """
        Test that an unknown charset does not make the rendering fail.
        """
        resp = Response(
            200, headers={"Content-Type": "text/plain; charset=bogus"}, body="grüße".encode()
        )
        self.assertTrue(resp.as_str(include_body=True).endswith("\r\n\r\ngrüße"))
        with self.assertRaises(LookupError):
            resp.body_str()


class ResponseOwnershipTestCase(unittest.TestCase):
    """
    tests for what a response keeps of the headers and the class it is
    given
    """

    def test_headers_subclass_is_kept(self) -> None:
        """
        Test that a Headers subclass — with its redaction set — survives
        the constructor and copy().
        """

        class _Strict(Headers):
            secret_names = Headers.secret_names | {"x-token"}

        headers = _Strict({"X-Token": "s3cr3t"})
        resp = Response(200, headers=headers)
        self.assertIs(type(resp.headers), _Strict)
        self.assertIsNot(resp.headers, headers)
        self.assertEqual(repr(resp.copy().headers), "_Strict(X-Token: ***)")

    def test_copy_of_a_subclass(self) -> None:
        """
        Test that copy() returns an instance of the response's own class.
        """

        class _Cached(Response):
            pass

        self.assertIs(type(_Cached(200).copy(status=304)), _Cached)

    def test_bytes_bodies_compare_by_content(self) -> None:
        """
        Test that responses with equal BytesBody bodies are equal.
        """
        self.assertEqual(Response(200, body=BytesBody(b"x")), Response(200, body=BytesBody(b"x")))
        self.assertNotEqual(Response(200, body=BytesBody(b"x")), Response(200, body=b"x"))


class ResponseFromStrTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.response.Response.from_str`
    """

    def test_parts(self) -> None:
        """
        Test that every part of the text ends up where it belongs.
        """
        resp = Response.from_str(
            "HTTP/1.0 404 Not Here At All\r\nContent-Type: text/plain\r\nSet-Cookie: a=1\r\n\r\ngone\r\n\r\nfor good"
        )
        self.assertEqual(resp.status, 404)
        self.assertEqual(resp.reason, "Not Here At All")
        self.assertEqual(resp.http_version, "HTTP/1.0")
        self.assertEqual(
            resp.headers.as_lines(), [("Content-Type", "text/plain"), ("Set-Cookie", "a=1")]
        )
        self.assertEqual(resp.body, "gone\r\n\r\nfor good")

    def test_status_line_without_phrase(self) -> None:
        """
        Test both spellings of a status line without a phrase: with the
        space the grammar asks for, and without it. The reason is the
        empty string — what the server sent —, not ``None``.
        """
        for text in ("HTTP/1.1 599 ", "HTTP/1.1 599"):
            with self.subTest(text=text):
                resp = Response.from_str(text)
                self.assertEqual(resp.status, 599)
                self.assertEqual(resp.reason, "")
                self.assertIsNone(resp.body)

    def test_round_trip(self) -> None:
        """
        Test that parsing what as_str() renders — and rendering it again
        — gives the same text.
        """
        responses = [
            Response(404, headers={"Content-Type": "text/plain"}, body="not here"),
            Response(599),
            Response(
                200, reason="Fine By Me", headers=[("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")]
            ),
            Response(204, http_version="HTTP/1.0"),
            Response(200, body="a\n\nb"),
        ]
        for resp in responses:
            for separator in ("\r\n", "\n"):
                with self.subTest(resp=repr(resp), separator=separator):
                    text = resp.as_str(include_body=True, separator=separator)
                    parsed = Response.from_str(text)
                    self.assertEqual(parsed.as_str(include_body=True, separator=separator), text)
                    self.assertEqual(parsed.status, resp.status)
                    self.assertEqual(parsed.phrase, resp.phrase)
                    self.assertEqual(parsed.headers, resp.headers)
                    self.assertEqual(parsed.body, resp.body)

    def test_invalid_text(self) -> None:
        """
        Test that a status line without a numeric status code, and an
        invalid header line, raise a ValueError.
        """
        for text in (
            "",
            "HTTP/1.1",
            "HTTP/1.1 OK",
            "HTTP/1.1 ²00 OK",
            " 200 OK",
            "HTTP/1.1 200 OK\nno colon here",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    Response.from_str(text)

    def test_subclass(self) -> None:
        """
        Test that the classmethod returns an instance of the class it is
        called on.
        """

        class _Cached(Response):
            pass

        self.assertIs(type(_Cached.from_str("HTTP/1.1 200 OK")), _Cached)


class ResponseStreamReadOnceTestCase(unittest.TestCase):
    """
    tests for a response whose streamed body has been read: it says so
    instead of passing for an empty one
    """

    def test_second_read_raises(self) -> None:
        """
        Test every synchronous way of reading the body a second time.
        """
        resp = Response(200, body=IterableBody(chunk for chunk in [b"a", b"b"]))
        self.assertEqual(resp.body_bytes(), b"ab")
        for read in (resp.body_bytes, resp.body_str, resp.body_file):
            with self.subTest(read=read.__name__):
                with self.assertRaisesRegex(RuntimeError, "has been read already"):
                    read()

    def test_save_body_after_a_read_leaves_the_file_alone(self) -> None:
        """
        Test the case that used to lose data: saving a body that has
        been read does not truncate the file.
        """
        resp = Response(200, body=IterableBody(chunk for chunk in [b"new"]))
        resp.body_bytes()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "download.bin"
            path.write_bytes(b"precious")
            with self.assertRaises(RuntimeError):
                resp.save_body(path)
            self.assertEqual(path.read_bytes(), b"precious")

    def test_body_file_first_keeps_the_body_readable(self) -> None:
        """
        Test the way out the error points to: after body_file() the
        body can be read any number of times.
        """
        with Response(200, body=IterableBody(chunk for chunk in [b"a", b"b"])) as resp:
            file = resp.body_file()
            assert file is not None
            self.assertEqual(file.read(), b"ab")
            self.assertEqual(resp.body_bytes(), b"ab")
            self.assertEqual(resp.body_bytes(), b"ab")


class ResponseBodyFileOptionsTestCase(unittest.TestCase):
    """
    tests for the spool options of
    :py:meth:`action0.req.response.Response.body_file`
    """

    def test_max_memory(self) -> None:
        """
        Test that the threshold of the spool can be chosen, and that
        the default keeps a small body in memory.
        """
        with Response(200, body=IterableBody([b"abc", b"def"])) as resp:
            resp.body_file()
            assert isinstance(resp.body, SpooledBody)
            self.assertFalse(resp.body.rolled)
        with Response(200, body=IterableBody([b"abc", b"def"])) as resp:
            resp.body_file(max_memory=4)
            assert isinstance(resp.body, SpooledBody)
            self.assertTrue(resp.body.rolled)

    def test_dir(self) -> None:
        """
        Test that the temporary file goes where it is told to: a
        directory that does not exist makes the rollover fail.
        """
        resp = Response(200, body=IterableBody([b"abc", b"def"]))
        with self.assertRaises(OSError):
            resp.body_file(max_memory=0, dir="/nonexistent/directory")
        with TemporaryDirectory() as tmp:
            with Response(200, body=IterableBody([b"abc", b"def"])) as resp:
                file = resp.body_file(max_memory=0, dir=tmp)
                assert file is not None
                self.assertEqual(file.read(), b"abcdef")

    def test_options_reach_the_spool_of_an_async_only_stream(self) -> None:
        """
        Test that the spool replacing a stream only aclose() can release
        gets the options too.
        """

        class _AsyncConnection(_Stream):
            async def aclose(self) -> None:
                pass

        resp = Response(200, body=_AsyncConnection())
        resp.body_file(max_memory=4)
        assert isinstance(resp.body, SpooledBody)
        self.assertTrue(resp.body.rolled)
        asyncio.run(resp.aclose())

    def test_producer_without_any_close(self) -> None:
        """
        Test that a producer with neither close() nor aclose() is
        spooled and simply let go.
        """
        with Response(200, body=_Stream()) as resp:
            file = resp.body_file()
            assert file is not None
            self.assertEqual(file.read(), b"chunk1chunk2")
            self.assertIs(type(resp.body), SpooledBody)


class ResponseAsyncBodyTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for the asynchronous body accessors of
    :py:class:`action0.req.response.Response`: abody_bytes(),
    abody_str() and abody_file()
    """

    def setUp(self) -> None:
        self.released: list[str] = []

    async def _release(self) -> None:
        self.released.append("released")

    @staticmethod
    async def _source() -> AsyncIterator[bytes]:
        yield b"chunk1"
        yield b"chunk2"

    async def test_abody_bytes_and_str(self) -> None:
        """
        Test that an async-only body is read, and plain bodies come out
        as with the synchronous accessors.
        """
        resp = Response(200, body=AsyncIterableBody(self._source(), aclose=self._release))
        self.assertEqual(await resp.abody_bytes(), b"chunk1chunk2")
        self.assertEqual(self.released, ["released"])
        resp = Response(200, body=AsyncIterableBody(self._source()))
        self.assertEqual(await resp.abody_str(), "chunk1chunk2")
        with self.assertRaisesRegex(RuntimeError, "has been read already"):
            await resp.abody_str()

        latin = {"Content-Type": "text/plain; charset=latin-1"}
        self.assertIsNone(await Response(204).abody_bytes())
        self.assertIsNone(await Response(204).abody_str())
        self.assertEqual(await Response(200, body="ä", headers=latin).abody_bytes(), b"\xe4")
        self.assertEqual(await Response(200, body=b"\xe4", headers=latin).abody_str(), "ä")

    async def test_abody_file_spools_an_async_stream(self) -> None:
        """
        Test that an async-only stream becomes a file: it is spooled,
        the spool replaces the body, the stream is closed — and the
        response is readable again and again afterwards.
        """
        stream = AsyncIterableBody(self._source(), aclose=self._release)
        async with Response(200, body=stream) as resp:
            file = await resp.abody_file()
            assert file is not None
            self.assertEqual(file.read(), b"chunk1chunk2")
            self.assertIs(type(resp.body), SpooledBody)
            self.assertEqual(self.released, ["released"])
            self.assertEqual(await resp.abody_bytes(), b"chunk1chunk2")
            self.assertEqual(resp.body_bytes(), b"chunk1chunk2")
            spool = resp.body
        assert isinstance(spool, SpooledBody)
        self.assertTrue(spool.closed)
        self.assertEqual(self.released, ["released"])

    async def test_abody_file_options(self) -> None:
        """
        Test that the spool options are passed on.
        """
        async with Response(200, body=AsyncIterableBody(self._source())) as resp:
            await resp.abody_file(max_memory=4)
            assert isinstance(resp.body, SpooledBody)
            self.assertTrue(resp.body.rolled)

    async def test_abody_file_of_plain_bodies(self) -> None:
        """
        Test the bodies that need no spooling: none, in-memory ones and
        a spooled one.
        """
        self.assertIsNone(await Response(204).abody_file())
        for body in (b"data", "data", BytesBody(b"data")):
            with self.subTest(body=type(body).__name__):
                resp = Response(200, body=body)
                file = await resp.abody_file()
                assert file is not None
                self.assertEqual(file.read(), b"data")
                self.assertIs(resp.body, body)
        with SpooledBody() as spooled:
            spooled.write(b"data")
            resp = Response(200, body=spooled)
            file = await resp.abody_file()
            assert file is not None
            self.assertEqual(file.read(), b"data")
            self.assertIs(resp.body, spooled)

    async def test_abody_file_of_a_file_body(self) -> None:
        """
        Test that a FileBody hands out its file without a copy — looked
        up in a worker thread — and that a non-seekable one is spooled.
        """
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.bin"
            path.write_bytes(b"data")
            body = FileBody(path)
            resp = Response(200, body=body)
            with _recorded_hops() as hops:
                file = await resp.abody_file()
            assert file is not None
            self.assertEqual(file.read(), b"data")
            self.assertIs(resp.body, body)
            self.assertEqual(hops, ["_file_view"])
            resp.close()
            self.assertTrue(file.closed)

        class _Pipe:
            def __init__(self) -> None:
                self._buffer = io.BytesIO(b"data")

            def seekable(self) -> bool:
                return False

            def read(self, size: int = -1) -> bytes:
                return self._buffer.read(size)

        async with Response(200, body=FileBody(cast("Any", _Pipe()))) as resp:
            file = await resp.abody_file()
            assert file is not None
            self.assertEqual(file.read(), b"data")
            self.assertIs(type(resp.body), SpooledBody)

    async def test_abody_file_closes_a_sync_producer(self) -> None:
        """
        Test that a producer without aclose() is released through its
        close().
        """

        class _Connection(_Stream):
            closed = 0

            def close(self) -> None:
                self.closed += 1

        connection = _Connection()
        async with Response(200, body=connection) as resp:
            file = await resp.abody_file()
            assert file is not None
            self.assertEqual(file.read(), b"chunk1chunk2")
            self.assertEqual(connection.closed, 1)

    async def test_abody_file_of_a_broken_stream(self) -> None:
        """
        Test that a stream breaking off raises, keeps the body as it
        was and leaves no spool behind.
        """

        async def broken() -> AsyncIterator[bytes]:
            yield b"chunk1"
            raise ConnectionResetError("gone")

        spools: list[SpooledBody] = []

        class _Recording(SpooledBody):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                spools.append(self)

        stream = AsyncIterableBody(broken(), aclose=self._release)
        resp = Response(200, body=stream)
        with mock.patch.object(response_module, "SpooledBody", _Recording):
            with self.assertRaises(ConnectionResetError):
                await resp.abody_file()
        self.assertIs(resp.body, stream)
        self.assertEqual(len(spools), 1)
        self.assertTrue(spools[0].closed)
        self.assertEqual(self.released, ["released"])

    async def test_abody_file_of_a_stream_read_before(self) -> None:
        """
        Test that a stream that has been read is refused, not spooled
        as an empty body.
        """
        resp = Response(200, body=AsyncIterableBody(self._source()))
        await resp.abody_bytes()
        with self.assertRaisesRegex(RuntimeError, "has been read already"):
            await resp.abody_file()
        self.assertIsInstance(resp.body, AsyncIterableBody)

    async def test_abody_file_with_a_plain_async_iterator(self) -> None:
        """
        Test a third-party producer whose achunks() is not an async
        generator: there is nothing to close after the read.
        """

        class _Chunks:
            def __init__(self) -> None:
                self._left = [b"chunk1", b"chunk2"]

            def __aiter__(self) -> "_Chunks":
                return self

            async def __anext__(self) -> bytes:
                if not self._left:
                    raise StopAsyncIteration
                return self._left.pop(0)

        class _Plain(_Stream):
            def achunks(self) -> AsyncIterator[bytes]:
                return _Chunks()

        async with Response(200, body=_Plain()) as resp:
            file = await resp.abody_file()
            assert file is not None
            self.assertEqual(file.read(), b"chunk1chunk2")


class ResponseFromStrPhraseTestCase(unittest.TestCase):
    """
    tests for a status line without a phrase read by
    :py:meth:`action0.req.response.Response.from_str`: no phrase is made
    up for it
    """

    def test_no_phrase_is_invented_for_a_registered_code(self) -> None:
        """
        Test that a status line with the space and nothing behind it
        comes back as it was, although the registry has a phrase for the
        code.
        """
        text = "HTTP/1.1 200 \r\nA: b"
        resp = Response.from_str(text)
        self.assertEqual(resp.reason, "")
        self.assertEqual(resp.phrase, "")
        self.assertEqual(resp.as_str(), text)
        self.assertEqual(repr(resp), "Response(200)")

    def test_status_line_without_the_space_gains_only_the_space(self) -> None:
        """
        Test the documented exception from the round trip: the space
        behind the status code is always written, a phrase is not added.
        """
        resp = Response.from_str("HTTP/2 200\r\nA: b")
        self.assertEqual(resp.reason, "")
        self.assertEqual(resp.as_str(), "HTTP/2 200 \r\nA: b")

    def test_constructed_response_still_falls_back_to_the_registry(self) -> None:
        """
        Test that ``None`` — the reason is not known — keeps meaning the
        registry phrase, and that the two are different responses.
        """
        self.assertEqual(Response(200).as_str(), "HTTP/1.1 200 OK")
        self.assertEqual(Response(200, reason="").as_str(), "HTTP/1.1 200 ")
        self.assertNotEqual(Response(200), Response.from_str("HTTP/1.1 200 "))

    def test_status_code_is_written_as_the_number_it_is(self) -> None:
        """
        Test the other documented exception: leading zeros are not kept.
        """
        self.assertEqual(Response.from_str("HTTP/1.1 0200 OK").as_str(), "HTTP/1.1 200 OK")


class ResponseStatusTypeTestCase(unittest.TestCase):
    """
    tests for what counts as a status of
    :py:class:`action0.req.response.Response` and how it is rendered
    """

    def test_bool_is_refused(self) -> None:
        """
        Test that a bool — an int to Python — is not taken for the
        status 1 or 0, neither by the constructor nor on assignment.
        """
        for value in (True, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TypeError, "got bool"):
                    Response(value)
        resp = Response(200)
        with self.assertRaises(TypeError):
            resp.status = True
        self.assertEqual(resp.status, 200)

    def test_int_enum_member_is_rendered_as_its_number(self) -> None:
        """
        Test that a member of somebody else's int enum — whose
        ``format()`` is its name — is written as the status code in the
        status line and in the repr, and is kept as it was set.
        """
        import enum

        class Code(int, enum.Enum):
            FINE = 200
            ODD = 599

        resp = Response(Code.FINE)
        self.assertEqual(resp.as_str(), "HTTP/1.1 200 OK")
        self.assertEqual(repr(resp), "Response(200 OK)")
        self.assertIs(resp.status, Code.FINE)
        self.assertEqual(Response(Code.ODD).as_str(), "HTTP/1.1 599 ")
        self.assertEqual(Response(Status.NOT_FOUND).as_str(), "HTTP/1.1 404 Not Found")


class ResponseStartLineSafetyTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.req.response.Response.as_str` refusing
    an HTTP version that would not stay one token of the status line
    """

    def test_whitespace_in_the_version_is_refused(self) -> None:
        """
        Test that a version cannot carry a status and a phrase of its
        own into the status line.
        """
        for version in ("HTTP/1.1 200 OK\tX", "HTTP/1.1 500", "HTTP/1.1\t", ""):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, "HTTP version"):
                    Response(200, http_version=version).as_str()

    def test_phrase_may_contain_spaces(self) -> None:
        """
        Test that the reason phrase, the last part of the line, is not
        held to that.
        """
        self.assertEqual(Response(200, reason="Fine By Me").as_str(), "HTTP/1.1 200 Fine By Me")


class ResponseAttributeSettersTestCase(unittest.TestCase):
    """
    tests for assigning to
    :py:attr:`action0.req.response.Response.headers`
    """

    def test_headers_mapping_is_converted(self) -> None:
        """
        Test that a dict assigned as the headers becomes a Headers
        instance: the charset is found whatever the casing, and the
        rendering works.
        """
        resp = Response(200, body=b"\xe9")
        resp.headers = {"content-type": "text/plain; charset=latin-1"}
        self.assertIsInstance(resp.headers, Headers)
        self.assertEqual(resp.body_str(), "é")
        self.assertEqual(
            resp.as_str(include_body=True, separator="\n"),
            "HTTP/1.1 200 OK\ncontent-type: text/plain; charset=latin-1\n\né",
        )

    def test_other_forms_and_instances(self) -> None:
        """
        Test a raw block and ``None``, and that a Headers instance is
        taken as it is.
        """
        resp = Response(200)
        resp.headers = "A: 1\r\nB: 2"
        self.assertEqual(dict(resp.headers), {"A": "1", "B": "2"})
        resp.headers = None
        self.assertEqual(len(resp.headers), 0)
        headers = Headers({"C": "3"})
        resp.headers = headers
        self.assertIs(resp.headers, headers)

    def test_invalid_headers_are_refused(self) -> None:
        """
        Test that what cannot be headers raises on assignment.
        """
        resp = Response(200, headers={"A": "1"})
        with self.assertRaises(TypeError):
            resp.headers = cast("Any", 5)
        self.assertEqual(dict(resp.headers), {"A": "1"})

    def test_copy_takes_instance_attributes_along(self) -> None:
        """
        Test that copy() keeps what has been set on the instance besides
        the parts of a response, as Request.copy() does.
        """
        resp = Response(200, headers={"A": "1"})
        marker = object()
        resp.__dict__["note"] = marker
        duplicate = resp.copy(status=404)
        self.assertIs(duplicate.__dict__["note"], marker)
        self.assertEqual(duplicate.status, 404)
        self.assertIsNot(duplicate.headers, resp.headers)


class ResponseCharsetTestCase(unittest.TestCase):
    """
    tests for the charset a response reads from its Content-Type, where
    that header is not as plain as usual
    """

    def test_semicolon_in_a_quoted_parameter(self) -> None:
        """
        Test that a ";charset=" inside the quoted value of another
        parameter is not taken for the charset parameter.
        """
        headers = {"Content-Type": 'multipart/form-data; boundary="a;charset=latin-1"'}
        self.assertEqual(Response(200, headers=headers, body="é").body_bytes(), b"\xc3\xa9")
        self.assertEqual(
            Request("https://h/", "POST", headers=headers, body="é").body_bytes(), b"\xc3\xa9"
        )
        headers = {"Content-Type": 'text/plain; x="a;charset=nonsense"; charset="latin-1"'}
        self.assertEqual(Response(200, headers=headers, body=b"\xe9").body_str(), "é")

    def test_rendering_does_not_raise_over_the_charset(self) -> None:
        """
        Test that a debug rendering falls back to utf-8 for every
        charset Python cannot decode with: an unknown one, a codec that
        is not for text, one that refuses to decode or to replace, and a
        name with a NUL — the header is the server's to write.
        """
        for charset in ("nonsense", "hex", "undefined", "idna", "utf\x008", "rot13"):
            with self.subTest(charset=charset):
                headers = Headers()
                # past the check for the NUL: this is about the body
                headers._lines.append(("Content-Type", f"text/plain; charset={charset}"))
                resp = Response(200, body=b"gr\xc3\xbc\xdf")
                resp.headers = headers
                self.assertTrue(resp.as_str(include_body=True).endswith("\r\n\r\ngrü\ufffd"))
                req = Request("https://h/", "POST", body=b"ok")
                req.headers = headers
                self.assertTrue(req.as_str(include_body=True).endswith("\r\n\r\nok"))


class ResponseBodyTypeTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for a response body of a type that is bytes-like but not a
    body: ``bytearray`` and ``memoryview``
    """

    async def test_every_way_to_the_body_says_what_is_wrong(self) -> None:
        """
        Test that the accessors, the file views and the save helpers
        raise a TypeError naming the type.
        """
        for body in (bytearray(b"x"), memoryview(b"x")):
            resp = Response(200, body=cast("Any", body))
            target = io.BytesIO()
            for access in (
                resp.body_bytes,
                resp.body_str,
                resp.body_producer,
                resp.body_file,
                lambda resp=resp: resp.as_str(include_body=True),
                lambda resp=resp, target=target: resp.save_body(target),
            ):
                with self.subTest(body=type(body).__name__, access=access):
                    with self.assertRaisesRegex(TypeError, "convert it with bytes"):
                        access()
            for aaccess in (resp.abody_bytes, resp.abody_str, resp.abody_file):
                with self.subTest(body=type(body).__name__, access=aaccess):
                    with self.assertRaisesRegex(TypeError, "convert it with bytes"):
                        await aaccess()
            with self.assertRaisesRegex(TypeError, "convert it with bytes"):
                await resp.asave_body(target)
            self.assertEqual(target.getvalue(), b"")
            # closing looks at no content
            resp.close()

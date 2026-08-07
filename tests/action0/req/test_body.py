import asyncio
import io
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import IO
from typing import cast

from action0.req import AsyncIterableBody
from action0.req import BodyProducer
from action0.req import BytesBody
from action0.req import FileBody
from action0.req import IterableBody


def _collect_achunks(producer: BodyProducer) -> list[bytes]:
    """
    Drain a producer's async iteration from synchronous test code.
    """

    async def collect() -> list[bytes]:
        return [chunk async for chunk in producer.achunks()]

    return asyncio.run(collect())


class _ReadOnlyStream:
    """
    A minimal non-seekable binary stream for the FileBody tests.
    """

    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def seekable(self) -> bool:
        return False


class BytesBodyTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.body.BytesBody`
    """

    def test_implements_body_producer(self) -> None:
        """
        Test that BytesBody structurally satisfies the runtime-checkable
        BodyProducer protocol.
        """
        self.assertIsInstance(BytesBody(b""), BodyProducer)

    def test_content_length(self) -> None:
        """
        Test that the content length is the number of bytes.
        """
        self.assertEqual(BytesBody(b"hello").content_length(), 5)
        self.assertEqual(BytesBody(b"").content_length(), 0)

    def test_as_bytes(self) -> None:
        """
        Test that as_bytes() returns the bytes as given.
        """
        self.assertEqual(BytesBody(b"hello").as_bytes(), b"hello")

    def test_chunks(self) -> None:
        """
        Test that the sync iteration yields the body as a single chunk.
        """
        self.assertEqual(list(BytesBody(b"hello").chunks()), [b"hello"])

    def test_achunks(self) -> None:
        """
        Test that the async iteration yields the body as a single chunk.
        """
        self.assertEqual(_collect_achunks(BytesBody(b"hello")), [b"hello"])


class FileBodyTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.body.FileBody`
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "body.bin"
        self.path.write_bytes(b"abcdef")

    def test_implements_body_producer(self) -> None:
        """
        Test that FileBody satisfies the BodyProducer protocol.
        """
        self.assertIsInstance(FileBody(self.path), BodyProducer)

    def test_chunk_size_must_be_positive(self) -> None:
        """
        Test that a non-positive chunk size is rejected.
        """
        with self.assertRaises(ValueError):
            FileBody(self.path, chunk_size=0)

    def test_path_source(self) -> None:
        """
        Test length, chunking and full reads for a path source (str and
        PathLike).
        """
        body = FileBody(self.path, chunk_size=4)
        self.assertEqual(body.content_length(), 6)
        self.assertEqual(list(body.chunks()), [b"abcd", b"ef"])
        self.assertEqual(body.as_bytes(), b"abcdef")
        self.assertEqual(FileBody(str(self.path)).as_bytes(), b"abcdef")

    def test_path_source_is_reiterable(self) -> None:
        """
        Test that a path source can be streamed multiple times.
        """
        body = FileBody(self.path, chunk_size=4)
        self.assertEqual(list(body.chunks()), list(body.chunks()))

    def test_path_source_achunks(self) -> None:
        """
        Test the async iteration for a path source.
        """
        body = FileBody(self.path, chunk_size=4)
        self.assertEqual(_collect_achunks(body), [b"abcd", b"ef"])

    def test_seekable_file_object_rewinds(self) -> None:
        """
        Test that a seekable file object is rewound per iteration and its
        length measurement restores the position.
        """
        file = io.BytesIO(b"abcdef")
        file.read(3)  # somewhere in the middle
        body = FileBody(file, chunk_size=4)
        self.assertEqual(body.content_length(), 6)
        self.assertEqual(file.tell(), 3)
        self.assertEqual(list(body.chunks()), [b"abcd", b"ef"])
        # re-iterable thanks to the rewind
        self.assertEqual(list(body.chunks()), [b"abcd", b"ef"])
        self.assertEqual(_collect_achunks(body), [b"abcd", b"ef"])
        # the file object is not closed by FileBody
        self.assertFalse(file.closed)

    def test_non_seekable_file_object(self) -> None:
        """
        Test that a non-seekable stream has an unknown length and is
        consumed from the current position, only once.
        """
        # cast: the minimal fake implements just the parts of IO[bytes]
        # that FileBody uses (read and seekable)
        body = FileBody(cast("IO[bytes]", _ReadOnlyStream(b"abcdef")), chunk_size=4)
        self.assertIsNone(body.content_length())
        self.assertEqual(list(body.chunks()), [b"abcd", b"ef"])
        self.assertEqual(list(body.chunks()), [])


class IterableBodyTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.body.IterableBody`
    """

    def test_implements_body_producer(self) -> None:
        """
        Test that IterableBody satisfies the BodyProducer protocol.
        """
        self.assertIsInstance(IterableBody([]), BodyProducer)

    def test_content_length_unknown(self) -> None:
        """
        Test that the length is always unknown.
        """
        self.assertIsNone(IterableBody([b"x"]).content_length())

    def test_list_source_is_reiterable(self) -> None:
        """
        Test chunking, async chunking and joining for a list source.
        """
        body = IterableBody([b"chunk1", b"chunk2"])
        self.assertEqual(list(body.chunks()), [b"chunk1", b"chunk2"])
        self.assertEqual(_collect_achunks(body), [b"chunk1", b"chunk2"])
        self.assertEqual(body.as_bytes(), b"chunk1chunk2")

    def test_generator_source_is_single_use(self) -> None:
        """
        Test that a generator source is consumed by the first read.
        """
        body = IterableBody(chunk for chunk in [b"chunk1", b"chunk2"])
        self.assertEqual(body.as_bytes(), b"chunk1chunk2")
        self.assertEqual(list(body.chunks()), [])


class AsyncIterableBodyTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.body.AsyncIterableBody`
    """

    @staticmethod
    def _agenerate() -> AsyncIterableBody:
        async def generate() -> AsyncIterator[bytes]:
            yield b"async"
            yield b"chunks"

        return AsyncIterableBody(generate())

    def test_implements_body_producer(self) -> None:
        """
        Test that AsyncIterableBody satisfies the BodyProducer protocol.
        """
        self.assertIsInstance(self._agenerate(), BodyProducer)

    def test_achunks(self) -> None:
        """
        Test the async iteration over an async generator source.
        """
        self.assertEqual(_collect_achunks(self._agenerate()), [b"async", b"chunks"])

    def test_content_length_unknown(self) -> None:
        """
        Test that the length is always unknown.
        """
        self.assertIsNone(self._agenerate().content_length())

    def test_sync_access_raises(self) -> None:
        """
        Test that the synchronous accessors raise a RuntimeError.
        """
        body = self._agenerate()
        with self.assertRaises(RuntimeError):
            body.chunks()
        with self.assertRaises(RuntimeError):
            body.as_bytes()

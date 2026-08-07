import asyncio
import unittest

from action0.req import BodyProducer
from action0.req import BytesBody


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

        async def collect() -> list[bytes]:
            return [chunk async for chunk in BytesBody(b"hello").achunks()]

        self.assertEqual(asyncio.run(collect()), [b"hello"])

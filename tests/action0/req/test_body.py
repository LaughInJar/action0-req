import asyncio
import contextlib
import gzip
import io
import json
import sys
import threading
import time
import unittest
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import IO
from typing import Any
from typing import Union
from typing import cast
from unittest import mock

import trio

from action0.req import AsyncIterableBody
from action0.req import BodyProducer
from action0.req import BytesBody
from action0.req import FileBody
from action0.req import IterableBody
from action0.req import SpooledBody
from action0.req import asave_body
from action0.req import body as body_module
from action0.req import save_body


def _collect_achunks(producer: BodyProducer) -> list[bytes]:
    """
    Drain a producer's async iteration from synchronous test code.
    """

    async def collect() -> list[bytes]:
        return [chunk async for chunk in producer.achunks()]

    return asyncio.run(collect())


@contextlib.contextmanager
def _recorded_thread_hops() -> Iterator[list[str]]:
    """
    Record the name of every function the body module hands to a worker
    thread, while still running it there — the way to pin down what
    blocks the event loop and what does not.
    """
    hops: list[str] = []

    def recording(real: Callable[..., Any]) -> Callable[..., Any]:
        async def dispatch(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
            hops.append(getattr(function, "__name__", repr(function)))
            return await real(function, *args, **kwargs)

        return dispatch

    # the plain dispatch, the one for cleanup that survives cancellation
    # and the one for reads that may be left behind
    with contextlib.ExitStack() as stack:
        for name in ("_in_thread", "_in_thread_shielded", "_in_thread_abandonable"):
            real = getattr(body_module, name)
            stack.enter_context(mock.patch.object(body_module, name, recording(real)))
        yield hops


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


class _Gate:
    """
    Holds a worker thread inside a function of the test's choosing:
    ``started`` is set once the thread has arrived there, and it goes on
    when ``release`` is set.
    """

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def wait(self) -> None:
        self.started.set()
        self.release.wait(10)


async def _cancel_while_held(
    test: unittest.TestCase, task: "asyncio.Future[Any]", gate: _Gate
) -> None:
    """
    Cancel an asyncio task whose worker thread is held at the gate, check
    that the task waits for the thread instead of going on without it,
    then let the thread go and see the cancellation arrive.
    """
    await asyncio.to_thread(gate.started.wait, 10)
    task.cancel()
    # real time, not just a turn of the loop: a task that does not wait
    # gets the chance to run its cleanup while the thread is still held
    await asyncio.sleep(0.05)
    test.assertFalse(task.done())
    gate.release.set()
    with test.assertRaises(asyncio.CancelledError):
        await task


class _RawTarget:
    """
    A write target behaving like a raw (unbuffered) file or a pipe: every
    ``write()`` takes a few bytes only and returns how many.
    """

    def __init__(self, accepts: int = 3) -> None:
        self.data = b""
        self.calls = 0
        self._accepts = accepts

    def write(self, data: bytes) -> int:
        self.calls += 1
        taken = data[: self._accepts]
        self.data += taken
        return len(taken)


class _SilentTarget:
    """
    A file-like write target whose ``write()`` returns nothing — common
    among hand-written ones.
    """

    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data


class _NonBlockingTarget(io.RawIOBase):
    """
    A raw file in non-blocking mode that is not ready: ``write()``
    returns ``None``, which says that nothing was written.
    """

    def writable(self) -> bool:
        return True

    def write(self, data: Any, /) -> None:
        return None


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


class IterableBodyCloseTestCase(unittest.TestCase):
    """
    tests for the ``close`` callback of :py:class:`action0.req.body.IterableBody`
    """

    def setUp(self) -> None:
        self.closed: list[str] = []

    def _release(self) -> None:
        self.closed.append("closed")

    def test_closed_after_full_consumption(self) -> None:
        """
        Test that reading the body to its end runs the callback, and that
        a later close() does not run it again.
        """
        body = IterableBody(iter([b"a", b"b"]), close=self._release)
        self.assertEqual(list(body.chunks()), [b"a", b"b"])
        self.assertEqual(self.closed, ["closed"])
        body.close()
        self.assertEqual(self.closed, ["closed"])

    def test_closed_when_iteration_is_abandoned(self) -> None:
        """
        Test that giving up midway — closing the chunk iterator — runs
        the callback.
        """
        body = IterableBody(iter([b"a", b"b"]), close=self._release)
        chunks = body.chunks()
        self.assertEqual(next(chunks), b"a")
        self.assertEqual(self.closed, [])
        cast("Generator[bytes]", chunks).close()
        self.assertEqual(self.closed, ["closed"])

    def test_close_of_a_body_never_read(self) -> None:
        """
        Test the case a generator's own ``finally`` cannot cover: the
        body is closed without ever having been iterated.
        """
        body = IterableBody(iter([b"a"]), close=self._release)
        body.close()
        body.close()
        self.assertEqual(self.closed, ["closed"])

    def test_context_manager(self) -> None:
        """
        Test that leaving the ``with`` block closes the body, also on an
        exception.
        """
        with IterableBody(iter([b"a"]), close=self._release) as body:
            self.assertIsInstance(body, IterableBody)
        self.assertEqual(self.closed, ["closed"])

        with self.assertRaises(KeyError):
            with IterableBody(iter([b"a"]), close=self._release):
                raise KeyError("boom")
        self.assertEqual(self.closed, ["closed", "closed"])

    def test_as_bytes_closes(self) -> None:
        """
        Test that reading the whole body at once runs the callback.
        """
        body = IterableBody(iter([b"a", b"b"]), close=self._release)
        self.assertEqual(body.as_bytes(), b"ab")
        self.assertEqual(self.closed, ["closed"])

    def test_achunks_closes(self) -> None:
        """
        Test that the asynchronous iteration runs the callback too.
        """
        body = IterableBody(iter([b"a", b"b"]), close=self._release)
        self.assertEqual(_collect_achunks(body), [b"a", b"b"])
        self.assertEqual(self.closed, ["closed"])

    def test_closed_when_the_source_fails(self) -> None:
        """
        Test that a stream breaking off midway still runs the callback.
        """

        def broken() -> Iterator[bytes]:
            yield b"a"
            raise ConnectionResetError("gone")

        body = IterableBody(broken(), close=self._release)
        with self.assertRaises(ConnectionResetError):
            list(body.chunks())
        self.assertEqual(self.closed, ["closed"])

    def test_failing_callback_is_not_run_twice(self) -> None:
        """
        Test that a callback that raised is dropped like one that
        succeeded.
        """

        def failing() -> None:
            self.closed.append("tried")
            raise OSError("cannot close")

        body = IterableBody([b"a"], close=failing)
        with self.assertRaises(OSError):
            body.close()
        body.close()
        self.assertEqual(self.closed, ["tried"])

    def test_without_callback(self) -> None:
        """
        Test that a plain body can be closed and stays re-iterable.
        """
        body = IterableBody([b"a", b"b"])
        body.close()
        self.assertEqual(list(body.chunks()), [b"a", b"b"])
        self.assertEqual(list(body.chunks()), [b"a", b"b"])

    def test_started_generator_is_closed(self) -> None:
        """
        Test that closing a body read only in part closes the generator
        behind it — its ``finally`` block runs, ahead of the callback —
        and that nothing is left to read.
        """
        released: list[str] = []

        def source() -> Iterator[bytes]:
            try:
                yield b"a"
                yield b"b"
            finally:
                released.append("generator")

        body = IterableBody(source(), close=lambda: released.append("callback"))
        chunks = body.chunks()
        self.assertEqual(next(chunks), b"a")
        body.close()
        self.assertEqual(released, ["generator", "callback"])
        self.assertEqual(list(chunks), [])
        self.assertEqual(list(body.chunks()), [])

    def test_generator_never_started_is_closed(self) -> None:
        """
        Test that a body over a generator cannot be read after close(),
        also when the generator had not been started.
        """
        body = IterableBody(chunk for chunk in (b"a", b"b"))
        body.close()
        self.assertEqual(list(body.chunks()), [])

    def test_close_while_the_generator_is_running(self) -> None:
        """
        Test that close() leaves a generator alone that is executing
        right now — here it calls close() itself — instead of failing
        with "generator already executing": the callback still runs.
        """
        released: list[bool] = []
        bodies: list[IterableBody] = []

        def source() -> Iterator[bytes]:
            yield b"a"
            bodies[0].close()
            yield b"b"

        bodies.append(IterableBody(source(), close=lambda: released.append(True)))
        self.assertEqual(list(bodies[0].chunks()), [b"a", b"b"])
        self.assertEqual(released, [True])


class AsyncIterableBodyCloseTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for the ``aclose`` callback of
    :py:class:`action0.req.body.AsyncIterableBody`
    """

    def setUp(self) -> None:
        self.closed: list[str] = []

    async def _release(self) -> None:
        await asyncio.sleep(0)
        self.closed.append("closed")

    @staticmethod
    async def _source() -> AsyncIterator[bytes]:
        yield b"a"
        yield b"b"

    async def test_closed_after_full_consumption(self) -> None:
        """
        Test that reading the body to its end awaits the callback once.
        """
        body = AsyncIterableBody(self._source(), aclose=self._release)
        self.assertEqual([chunk async for chunk in body.achunks()], [b"a", b"b"])
        self.assertEqual(self.closed, ["closed"])
        await body.aclose()
        self.assertEqual(self.closed, ["closed"])

    async def test_closed_when_iteration_is_abandoned(self) -> None:
        """
        Test that closing the chunk iterator midway awaits the callback.
        """
        body = AsyncIterableBody(self._source(), aclose=self._release)
        chunks = body.achunks()
        self.assertEqual(await anext(chunks), b"a")
        self.assertEqual(self.closed, [])
        await cast("Any", chunks).aclose()
        self.assertEqual(self.closed, ["closed"])

    async def test_aclose_of_a_body_never_read(self) -> None:
        """
        Test that a body that was never iterated can be closed, once.
        """
        body = AsyncIterableBody(self._source(), aclose=self._release)
        await body.aclose()
        await body.aclose()
        self.assertEqual(self.closed, ["closed"])

    async def test_async_context_manager(self) -> None:
        """
        Test that leaving the ``async with`` block closes the body.
        """
        async with AsyncIterableBody(self._source(), aclose=self._release) as body:
            self.assertIsInstance(body, AsyncIterableBody)
        self.assertEqual(self.closed, ["closed"])

    async def test_without_callback(self) -> None:
        """
        Test that closing a body without a callback closes the
        generator behind it: nothing is left to read.
        """
        body = AsyncIterableBody(self._source())
        await body.aclose()
        await body.aclose()
        self.assertEqual([chunk async for chunk in body.achunks()], [])

    async def test_started_generator_is_closed(self) -> None:
        """
        Test that closing a body read only in part closes the async
        generator behind it — its ``finally`` block runs, ahead of the
        callback — and that nothing is left to read.
        """

        async def source() -> AsyncIterator[bytes]:
            try:
                yield b"a"
                yield b"b"
            finally:
                self.closed.append("generator")

        body = AsyncIterableBody(source(), aclose=self._release)
        chunks = body.achunks()
        self.assertEqual(await anext(chunks), b"a")
        await body.aclose()
        self.assertEqual(self.closed, ["generator", "closed"])
        self.assertEqual([chunk async for chunk in chunks], [])

    async def test_aclose_while_another_task_reads(self) -> None:
        """
        Test that aclose() can be used to abort a read from outside: a
        generator another task is waiting in is left alone — closing it
        would raise "already running" — while the callback releases what
        it reads from. The generator ends when its reader does.
        """
        waiting = asyncio.Event()

        async def source() -> AsyncIterator[bytes]:
            try:
                yield b"a"
                waiting.set()
                await asyncio.sleep(3600)
            finally:
                self.closed.append("generator")

        body = AsyncIterableBody(source(), aclose=self._release)

        async def read() -> list[bytes]:
            return [chunk async for chunk in body.achunks()]

        reader = asyncio.ensure_future(read())
        await waiting.wait()
        await body.aclose()
        self.assertEqual(self.closed, ["closed"])
        reader.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await reader
        self.assertEqual(self.closed, ["closed", "generator"])

    async def test_sync_close_of_a_generator_read_in_part(self) -> None:
        """
        Test that the synchronous close() does not pass over an async
        generator that has been started: only aclose() can run what it
        holds in a ``finally`` block. One never started holds nothing.
        """
        body = AsyncIterableBody(self._source())
        body.close()
        chunks = body.achunks()
        self.assertEqual(await anext(chunks), b"a")
        with self.assertRaises(RuntimeError):
            body.close()
        await body.aclose()
        body.close()

    async def test_sync_close(self) -> None:
        """
        Test that the synchronous close() refuses while the callback is
        still to run — it cannot be awaited there — and does nothing when
        there is nothing (left) to release.
        """
        AsyncIterableBody(self._source()).close()

        body = AsyncIterableBody(self._source(), aclose=self._release)
        with self.assertRaises(RuntimeError):
            body.close()
        self.assertEqual(self.closed, [])
        await body.aclose()
        body.close()
        self.assertEqual(self.closed, ["closed"])


class SpooledBodyTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.body.SpooledBody`
    """

    def test_implements_body_producer(self) -> None:
        """
        Test that SpooledBody structurally satisfies the BodyProducer
        protocol.
        """
        with SpooledBody() as body:
            self.assertIsInstance(body, BodyProducer)

    def test_arguments_are_validated(self) -> None:
        """
        Test that a negative threshold and a non-positive chunk size are
        refused.
        """
        with self.assertRaises(ValueError):
            SpooledBody(max_memory=-1)
        with self.assertRaises(ValueError):
            SpooledBody(chunk_size=0)

    def test_stays_in_memory_up_to_the_threshold(self) -> None:
        """
        Test that the data moves to the file with the write that exceeds
        the threshold — not with the one that reaches it.
        """
        with SpooledBody(max_memory=4) as body:
            self.assertEqual(body.write(b"ab"), 2)
            self.assertEqual(body.write(b"cd"), 2)
            self.assertFalse(body.rolled)
            body.write(b"e")
            self.assertTrue(body.rolled)
            self.assertEqual(body.as_bytes(), b"abcde")
            self.assertEqual(body.content_length(), 5)

    def test_zero_threshold_goes_to_the_file_at_once(self) -> None:
        """
        Test that ``max_memory=0`` uses the file from the first byte on,
        while an empty write alone creates no file.
        """
        with SpooledBody(max_memory=0) as body:
            body.write(b"")
            self.assertFalse(body.rolled)
            body.write(b"a")
            self.assertTrue(body.rolled)

    def test_file_is_seekable_and_rewound(self) -> None:
        """
        Test the file view: seekable, positioned at the start by every
        call, in memory as well as on disk.
        """
        for max_memory in (1024, 0):
            with self.subTest(max_memory=max_memory), SpooledBody(max_memory=max_memory) as body:
                body.write(b"abcdef")
                file = body.file()
                self.assertTrue(file.seekable())
                self.assertEqual(file.read(2), b"ab")
                file.seek(4)
                self.assertEqual(file.read(), b"ef")
                self.assertEqual(file.tell(), 6)
                self.assertEqual(file.seek(-2, io.SEEK_END), 4)
                self.assertEqual(file.seek(-1, io.SEEK_CUR), 3)
                self.assertEqual(file.read(1), b"d")
                with self.assertRaises(ValueError):
                    file.seek(-1)
                with self.assertRaises(ValueError):
                    file.seek(0, 3)
                # a view of its own per call, each starting at the beginning
                other = body.file()
                self.assertIsNot(other, file)
                self.assertEqual(other.read(), b"abcdef")
                self.assertEqual(file.tell(), 4)

    def test_reading_is_repeatable_and_chunked(self) -> None:
        """
        Test that the body is re-iterable, in chunks of the chunk size.
        """
        with SpooledBody(max_memory=0, chunk_size=4) as body:
            body.write(b"abcdef")
            self.assertEqual(list(body.chunks()), [b"abcd", b"ef"])
            self.assertEqual(list(body.chunks()), [b"abcd", b"ef"])
            self.assertEqual(body.as_bytes(), b"abcdef")

    def test_write_after_read_appends(self) -> None:
        """
        Test that a write following a read goes to the end and does not
        overwrite what a reader has rewound to.
        """
        for max_memory in (1024, 0):
            with self.subTest(max_memory=max_memory), SpooledBody(max_memory=max_memory) as body:
                body.write(b"abc")
                self.assertEqual(body.file().read(1), b"a")
                body.write(b"def")
                self.assertEqual(body.as_bytes(), b"abcdef")
                self.assertEqual(body.content_length(), 6)

    def test_write_after_reading_an_earlier_file_handle(self) -> None:
        """
        Test that an append goes to the end also when the position was
        moved through a file handle obtained before the previous write —
        file() hands out the body's own file object, so every handle
        moves the same position.
        """
        for max_memory in (1024, 0):
            with self.subTest(max_memory=max_memory), SpooledBody(max_memory=max_memory) as body:
                body.write(b"aaaa")
                file = body.file()
                body.write(b"bbbb")
                file.seek(0)
                self.assertEqual(file.read(2), b"aa")
                body.write(b"cc")
                self.assertEqual(body.as_bytes(), b"aaaabbbbcc")
                self.assertEqual(body.content_length(), 10)

    def test_file_is_a_read_only_view(self) -> None:
        """
        Test that the file object neither writes nor has a descriptor:
        nothing done through it changes the body, whose size and place
        (memory or disk) only write() decides.
        """
        with SpooledBody() as body:
            body.write(b"abc")
            file = body.file()
            self.assertTrue(file.readable())
            self.assertFalse(file.writable())
            with self.assertRaises(io.UnsupportedOperation):
                file.write(b"x")
            with self.assertRaises(io.UnsupportedOperation):
                file.truncate(0)
            with self.assertRaises(io.UnsupportedOperation):
                file.fileno()
            self.assertFalse(body.rolled)
            self.assertEqual(body.content_length(), 3)
            self.assertEqual(body.as_bytes(), b"abc")

    def test_readers_do_not_disturb_each_other(self) -> None:
        """
        Test that every iteration and every file view has a position of
        its own — overlapping readers each get the whole body, also what
        is written while they read.
        """
        for max_memory in (1024, 0):
            with self.subTest(max_memory=max_memory):
                with SpooledBody(max_memory=max_memory, chunk_size=4) as body:
                    body.write(b"0123456789abcdef")
                    first = body.chunks()
                    second = body.chunks()
                    self.assertEqual(next(first), b"0123")
                    self.assertEqual(next(second), b"0123")
                    view = body.file()
                    self.assertEqual(view.read(6), b"012345")
                    self.assertEqual(next(first), b"4567")
                    body.write(b"gh")
                    self.assertEqual(b"".join(second), b"456789abcdefgh")
                    self.assertEqual(b"".join(first), b"89abcdefgh")
                    self.assertEqual(view.read(), b"6789abcdefgh")
                    self.assertEqual(body.as_bytes(), b"0123456789abcdefgh")

    def test_file_feeds_gzip_and_json(self) -> None:
        """
        Test the use cases the file view exists for: a decompressor and a
        parser reading from a file object.
        """
        payload = {"items": list(range(1000))}
        for max_memory, rolled in ((1048576, False), (16, True)):
            with self.subTest(rolled=rolled), SpooledBody(max_memory=max_memory) as body:
                body.write(gzip.compress(json.dumps(payload).encode()))
                self.assertEqual(body.rolled, rolled)
                # no mode given: GzipFile takes it from the file object,
                # which must therefore not look like a file to write to
                with gzip.GzipFile(fileobj=body.file()) as unzipped:
                    self.assertEqual(json.load(unzipped), payload)
                # the decompressor does not close the file it was given
                self.assertFalse(body.closed)

    def test_file_reports_a_read_mode(self) -> None:
        """
        Test that the file object's ``mode`` starts with "r" in memory
        and on disk — consumers like GzipFile decide by it whether to
        read or to write.
        """
        for max_memory in (1024, 0):
            with self.subTest(max_memory=max_memory), SpooledBody(max_memory=max_memory) as body:
                body.write(b"abc")
                self.assertTrue(body.file().mode.startswith("r"), body.file().mode)

    def test_directory(self) -> None:
        """
        Test that the temporary file is created in the given directory
        (path objects welcome) — observable through a missing one.
        """
        with TemporaryDirectory() as tmp:
            with SpooledBody(max_memory=0, dir=Path(tmp)) as body:
                body.write(b"abc")
                self.assertEqual(body.as_bytes(), b"abc")
            with SpooledBody(max_memory=0, dir=Path(tmp) / "missing") as body:
                with self.assertRaises(FileNotFoundError):
                    body.write(b"abc")

    def test_close(self) -> None:
        """
        Test that close() is repeatable and that a closed body refuses
        every access.
        """
        body = SpooledBody(max_memory=0)
        body.write(b"abc")
        self.assertFalse(body.closed)
        body.close()
        body.close()
        self.assertTrue(body.closed)
        with self.assertRaises(ValueError):
            body.write(b"d")
        with self.assertRaises(ValueError):
            body.file()
        with self.assertRaises(ValueError):
            body.as_bytes()

    def test_write_to_a_closed_body_leaves_it_closed(self) -> None:
        """
        Test that a write to a closed body raises every time and leaves
        it closed — also the write that would move the data to a file:
        no temporary file may be created for a body that is gone.
        """
        for max_memory in (1024, 4, 0):
            with self.subTest(max_memory=max_memory):
                body = SpooledBody(max_memory=max_memory)
                body.close()
                for _ in range(2):
                    with self.assertRaises(ValueError):
                        body.write(b"abcdef")
                    self.assertTrue(body.closed)
                    self.assertFalse(body.rolled)
                self.assertEqual(body.content_length(), 0)

    def test_file_of_a_closed_body(self) -> None:
        """
        Test that a file view cannot read from a body that has been
        closed, and that closing the view afterwards is harmless.
        """
        body = SpooledBody()
        body.write(b"abc")
        file = body.file()
        body.close()
        with self.assertRaises(ValueError):
            file.read()
        file.close()
        self.assertTrue(body.closed)

    def test_closing_the_file_leaves_the_body_alone(self) -> None:
        """
        Test that the file object is a view, not the body's file: closing
        it — directly, or by a wrapper that closes what it wraps — does
        not discard the data.
        """
        with SpooledBody() as body:
            body.write(b"abc")
            body.file().close()
            with io.TextIOWrapper(body.file(), encoding="ascii") as text:
                self.assertEqual(text.read(), "abc")
            self.assertFalse(body.closed)
            self.assertEqual(body.file().read(), b"abc")

    def test_context_manager(self) -> None:
        """
        Test that leaving the ``with`` block closes the body.
        """
        with SpooledBody() as body:
            body.write(b"abc")
        self.assertTrue(body.closed)


class SpooledBodyAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for the asynchronous side of :py:class:`action0.req.body.SpooledBody`:
    what runs inline and what is handed to a worker thread
    """

    async def test_awrite_in_memory_is_inline(self) -> None:
        """
        Test that writes below the threshold never leave the event loop.
        """
        with _recorded_thread_hops() as hops:
            async with SpooledBody(max_memory=8) as body:
                self.assertEqual(await body.awrite(b"abcd"), 4)
                self.assertEqual(await body.awrite(b"efgh"), 4)
                self.assertFalse(body.rolled)
                self.assertEqual(hops, [])

    async def test_awrite_on_disk_uses_a_thread(self) -> None:
        """
        Test that the rollover and every write after it run in a worker
        thread — one hop per call.
        """
        with _recorded_thread_hops() as hops:
            async with SpooledBody(max_memory=4) as body:
                await body.awrite(b"abcd")
                await body.awrite(b"e")  # exceeds the threshold: rollover
                await body.awrite(b"f")
                self.assertTrue(body.rolled)
                self.assertEqual(hops, ["write", "write"])
                self.assertEqual(body.content_length(), 6)

    async def test_achunks(self) -> None:
        """
        Test that reading back yields the same chunks from memory
        (inline) and from the file (through worker threads).
        """
        for max_memory, threaded in ((1024, False), (0, True)):
            with self.subTest(max_memory=max_memory):
                async with SpooledBody(max_memory=max_memory, chunk_size=4) as body:
                    await body.awrite(b"abcdef")
                    with _recorded_thread_hops() as hops:
                        chunks = [chunk async for chunk in body.achunks()]
                    self.assertEqual(chunks, [b"abcd", b"ef"])
                    self.assertEqual(bool(hops), threaded)

    async def test_aclose(self) -> None:
        """
        Test that aclose() closes the body, in a worker thread only when
        there is a file to remove, and is repeatable.
        """
        for max_memory, expected in ((1024, []), (0, ["close"])):
            with self.subTest(max_memory=max_memory):
                body = SpooledBody(max_memory=max_memory)
                await body.awrite(b"abc")
                with _recorded_thread_hops() as hops:
                    await body.aclose()
                    await body.aclose()
                self.assertTrue(body.closed)
                self.assertEqual(hops, expected)

    async def test_async_context_manager(self) -> None:
        """
        Test that leaving the ``async with`` block closes the body.
        """
        async with SpooledBody() as body:
            await body.awrite(b"abc")
        self.assertTrue(body.closed)

    async def test_concurrent_readers(self) -> None:
        """
        Test that tasks reading the same body at the same time — a cached
        response served twice — each get all of it, in memory and from
        the file (where the reads run in worker threads).
        """
        data = bytes(range(256)) * 64
        for max_memory in (len(data), 0):
            with self.subTest(max_memory=max_memory):
                async with SpooledBody(max_memory=max_memory, chunk_size=1000) as body:
                    await body.awrite(data)

                    async def read(body: SpooledBody = body) -> bytes:
                        return b"".join([chunk async for chunk in body.achunks()])

                    results = await asyncio.gather(read(), read(), read())
                    self.assertEqual(results, [data, data, data])


class OwnedFileTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.body._OwnedFile`, which decides who
    closes the file FileBody.achunks() has opened
    """

    def test_release(self) -> None:
        """
        Test that the consumer is told to close a file that is open and
        idle — not one that was never opened —, and that a read arriving
        after the release finds nothing.
        """
        with TemporaryDirectory() as directory:
            path = Path(directory) / "data.bin"
            path.write_bytes(b"abc")

            self.assertFalse(body_module._OwnedFile(path).release())

            owned = body_module._OwnedFile(path)
            owned.open()
            self.assertEqual(owned.read(2), b"ab")
            self.assertTrue(owned.release())
            self.assertEqual(owned.read(2), b"")
            owned.close()


class InThreadTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.req.body._in_thread`, the worker-thread
    dispatch behind every asynchronous file operation
    """

    def test_asyncio_runs_in_another_thread(self) -> None:
        """
        Test that below asyncio the function runs off the loop's thread
        and its result (and arguments) pass through.
        """

        def work(base: int, *, extra: int) -> tuple[int, int]:
            return threading.get_ident(), base + extra

        async def main() -> tuple[int, int]:
            return await body_module._in_thread(work, 1, extra=2)

        thread, result = asyncio.run(main())
        self.assertNotEqual(thread, threading.get_ident())
        self.assertEqual(result, 3)

    def test_trio_runs_in_another_thread(self) -> None:
        """
        Test that below trio — no running asyncio loop, trio imported —
        the function runs in a worker thread of trio and its result (and
        arguments) pass through.
        """

        def work(base: int, *, extra: int) -> tuple[int, int]:
            return threading.get_ident(), base + extra

        async def main() -> tuple[int, int]:
            return await body_module._in_thread(work, 1, extra=2)

        thread, result = trio.run(main)
        self.assertNotEqual(thread, threading.get_ident())
        self.assertEqual(result, 3)

    def test_outside_any_event_loop(self) -> None:
        """
        Test that without asyncio and without trio the "no running event
        loop" error of asyncio surfaces.
        """
        with mock.patch.dict(sys.modules):
            sys.modules.pop("trio", None)
            for dispatch in (body_module._in_thread, body_module._in_thread_shielded):
                coroutine = dispatch(int, "7")
                with self.assertRaises(RuntimeError):
                    coroutine.send(None)


class InThreadShieldedTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.req.body._in_thread_shielded`, the
    dispatch for cleanup that has to run although the task is cancelled
    """

    def test_result_and_error_pass_through(self) -> None:
        """
        Test that without any cancellation it behaves like the plain
        dispatch, on asyncio and on trio: the result is returned, an
        error of the function is raised.
        """

        async def main() -> int:
            with self.assertRaises(ValueError):
                await body_module._in_thread_shielded(int, "x")
            return await body_module._in_thread_shielded(int, "7", base=8)

        self.assertEqual(asyncio.run(main()), 7)
        self.assertEqual(trio.run(main), 7)

    def test_asyncio_cancellation_waits_for_the_function(self) -> None:
        """
        Test that cancelling the task — even repeatedly, as anyio does —
        neither abandons the function nor gets lost: the task ends
        cancelled once the function has finished. On asyncio that holds
        for the plain dispatch too.
        """

        async def main(dispatch: Callable[..., Any]) -> None:
            started = threading.Event()
            release = threading.Event()
            finished: list[bool] = []

            def work() -> None:
                started.set()
                release.wait(10)
                finished.append(True)

            task = asyncio.ensure_future(dispatch(work))
            await asyncio.to_thread(started.wait, 10)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(finished, [True])

        for dispatch in (body_module._in_thread, body_module._in_thread_shielded):
            with self.subTest(dispatch=dispatch.__name__):
                asyncio.run(main(dispatch))

    def test_trio_plain_dispatch_does_not_start_when_cancelled(self) -> None:
        """
        Test the difference between the two below trio: in a scope that
        is cancelled already the plain dispatch does not run the function
        at all.
        """
        finished: list[bool] = []

        async def main() -> bool:
            with trio.CancelScope() as scope:
                scope.cancel()
                await body_module._in_thread(finished.append, True)
            return scope.cancelled_caught

        self.assertTrue(trio.run(main))
        self.assertEqual(finished, [])

    def test_trio_runs_in_a_cancelled_scope(self) -> None:
        """
        Test that below trio the function runs although the surrounding
        scope is cancelled already — where trio refuses to start a plain
        worker thread — and that the cancellation is delivered after it.
        """
        finished: list[bool] = []

        async def main() -> bool:
            with trio.CancelScope() as scope:
                scope.cancel()
                await body_module._in_thread_shielded(finished.append, True)
                await trio.lowlevel.checkpoint()
                self.fail("the cancellation got lost")  # pragma: no cover
            return scope.cancelled_caught

        self.assertTrue(trio.run(main))
        self.assertEqual(finished, [True])


class TrioCancellationTestCase(unittest.TestCase):
    """
    tests for the cleanup of the asynchronous file operations when a trio
    scope is cancelled — a timeout is the everyday case
    """

    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "download.bin"

    def test_asave_body_writes_what_arrived_and_closes(self) -> None:
        """
        Test that a download running into a timeout leaves what has
        arrived in the file, and the file closed.
        """

        async def stalling() -> AsyncIterator[bytes]:
            yield b"abc"
            await trio.sleep_forever()

        async def main() -> bool:
            with trio.move_on_after(0.05) as scope:
                await asave_body(AsyncIterableBody(stalling()), self.path)
            return scope.cancelled_caught

        with _recorded_thread_hops() as hops:
            self.assertTrue(trio.run(main))
        self.assertEqual(hops, ["_open_and_write", "close"])
        self.assertEqual(self.path.read_bytes(), b"abc")

    def test_asave_body_keeps_a_batch_trio_did_not_write(self) -> None:
        """
        Test that data arriving after the deadline, without a checkpoint
        in between, is not lost: trio refuses to start the flush of the
        full batch, and the final flush writes it.
        """
        scopes: list[trio.CancelScope] = []

        async def source() -> AsyncIterator[bytes]:
            yield b"a"  # written along with the open
            scopes[0].cancel()
            yield b"x" * 70000  # a full batch

        async def main() -> bool:
            with trio.CancelScope() as scope:
                scopes.append(scope)
                await asave_body(AsyncIterableBody(source()), self.path)
            return scope.cancelled_caught

        with _recorded_thread_hops() as hops:
            self.assertTrue(trio.run(main))
        # the first write_pending is the one that was refused
        self.assertEqual(hops, ["_open_and_write", "write_pending", "write_pending", "close"])
        self.assertEqual(self.path.read_bytes(), b"a" + b"x" * 70000)

    def test_spooled_body_is_closed(self) -> None:
        """
        Test that ``async with`` removes the temporary file of a spooled
        body also when the block is left by a timeout.
        """
        bodies: list[SpooledBody] = []

        async def main() -> bool:
            with trio.move_on_after(0.05) as scope:
                async with SpooledBody(max_memory=0) as body:
                    bodies.append(body)
                    await body.awrite(b"abc")
                    await trio.sleep_forever()
            return scope.cancelled_caught

        self.assertTrue(trio.run(main))
        self.assertTrue(bodies[0].closed)

    def test_file_body_closes_its_file(self) -> None:
        """
        Test that a path-based FileBody closes the file it opened when
        its consumer runs into a timeout.
        """
        self.path.write_bytes(b"abcdef")

        async def main() -> bool:
            # achunks() is annotated as a plain iterator; it is a generator
            generator = cast(
                "AsyncGenerator[bytes, None]", FileBody(self.path, chunk_size=4).achunks()
            )
            with trio.move_on_after(0.05) as scope:
                async with contextlib.aclosing(generator) as chunks:
                    async for _chunk in chunks:
                        await trio.sleep_forever()
            return scope.cancelled_caught

        with _recorded_thread_hops() as hops:
            self.assertTrue(trio.run(main))
        self.assertEqual(hops, ["open", "read", "close"])

    def test_stalled_read_does_not_block_a_timeout(self) -> None:
        """
        Test that a read which does not return — a pipe nobody writes to
        — is left behind when the deadline passes, instead of being
        waited for.
        """
        gate = _Gate()

        class _Stalled:
            def seekable(self) -> bool:
                return False

            def read(self, size: int = -1) -> bytes:
                gate.wait()
                return b""

        async def main() -> bool:
            with trio.move_on_after(0.05) as scope:
                async for _chunk in FileBody(cast("IO[bytes]", _Stalled())).achunks():
                    pass  # pragma: no cover
            return scope.cancelled_caught

        started = time.monotonic()
        try:
            self.assertTrue(trio.run(main))
            self.assertTrue(gate.started.is_set())
            # long before the held read would give up by itself
            self.assertLess(time.monotonic() - started, 5)
        finally:
            gate.release.set()


class AsyncioCancellationTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for what a cancellation on asyncio leaves behind: a worker
    thread cannot be stopped, so the file operation under way has to be
    finished before the cleanup — or anything else — touches the file
    """

    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "download.bin"

    async def test_flush_is_finished_before_the_file_is_closed(self) -> None:
        """
        Test that a cancellation arriving while a full batch is being
        written does not close the file under the write: the batch ends
        up in the file.
        """
        gate = _Gate()
        files: list[io.FileIO] = []

        class _Held(io.FileIO):
            def write(self, data: Any, /) -> int:
                if len(data) > 1:
                    gate.wait()
                return super().write(data)

        def open_held(path: Any, mode: str = "rb") -> io.FileIO:
            files.append(_Held(path, mode))
            return files[-1]

        async def stream() -> AsyncIterator[bytes]:
            yield b"a"  # written along with the open
            yield b"x" * 70000  # a full batch, flushed while the stream goes on
            await asyncio.sleep(3600)

        with mock.patch.object(body_module, "_open_binary", open_held):
            task = asyncio.ensure_future(asave_body(AsyncIterableBody(stream()), self.path))
            await _cancel_while_held(self, task, gate)
        self.assertTrue(files[0].closed)
        self.assertEqual(self.path.read_bytes(), b"a" + b"x" * 70000)

    async def test_awrite_is_finished_before_the_body_is_closed(self) -> None:
        """
        Test that a cancellation arriving during the write that moves a
        spooled body to its file lets the write finish first — closing
        the body under it would leave a temporary file behind.
        """
        gate = _Gate()
        rollover = body_module._Spool.rollover

        def held(spool: Any) -> None:
            gate.wait()
            rollover(spool)

        body = SpooledBody(max_memory=4)

        async def write() -> None:
            async with body:
                await body.awrite(b"abcdef")

        with mock.patch.object(body_module._Spool, "rollover", held):
            task = asyncio.ensure_future(write())
            await _cancel_while_held(self, task, gate)
        # rolled over and written, then closed — not the other way round
        self.assertTrue(body.rolled)
        self.assertEqual(body.content_length(), 6)
        self.assertTrue(body.closed)

    async def test_file_opened_under_cancellation_is_closed(self) -> None:
        """
        Test that a file whose opening was under way when the
        cancellation arrived is not lost: asave_body() has written the
        chunk it had and closed the file, FileBody.achunks() has closed
        it.
        """

        async def read() -> list[bytes]:
            return [chunk async for chunk in FileBody(self.path).achunks()]

        self.path.write_bytes(b"old")
        calls: list[Callable[[], Any]] = [lambda: asave_body(b"abc", self.path), read]
        for call in calls:
            gate = _Gate()
            files: list[IO[bytes]] = []

            def open_held(
                path: Any, mode: str = "rb", gate: _Gate = gate, files: list[IO[bytes]] = files
            ) -> IO[bytes]:
                gate.wait()
                files.append(open(path, mode))
                return files[-1]

            with mock.patch.object(body_module, "_open_binary", open_held):
                task = asyncio.ensure_future(call())
                await _cancel_while_held(self, task, gate)
            self.assertTrue(files[0].closed)
            self.assertEqual(self.path.read_bytes(), b"abc")

    async def test_stalled_read_does_not_block_a_timeout(self) -> None:
        """
        Test that a read which does not return — a pipe nobody writes to
        — is left behind when the task is cancelled, instead of being
        waited for: a timeout has to work exactly then.
        """
        gate = _Gate()

        class _Stalled:
            def seekable(self) -> bool:
                return False

            def read(self, size: int = -1) -> bytes:
                gate.wait()
                return b""

        async def read() -> list[bytes]:
            body = FileBody(cast("IO[bytes]", _Stalled()))
            return [chunk async for chunk in body.achunks()]

        started = time.monotonic()
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(read(), 0.05)
            self.assertTrue(gate.started.is_set())
            # long before the held read would give up by itself
            self.assertLess(time.monotonic() - started, 5)
        finally:
            gate.release.set()

    async def test_abandoned_read_closes_the_file(self) -> None:
        """
        Test that the file a path-based FileBody has opened is closed
        although its consumer was cancelled during a read: not by the
        consumer, which does not wait, but by the read left behind — as
        soon as it returns.
        """
        gate = _Gate()
        files: list[io.FileIO] = []

        class _Held(io.FileIO):
            def read(self, size: Union[int, None] = -1, /) -> bytes:
                gate.wait()
                return super().read(size)

        def open_held(path: Any, mode: str = "rb") -> io.FileIO:
            files.append(_Held(path, mode))
            return files[-1]

        async def read() -> list[bytes]:
            return [chunk async for chunk in FileBody(self.path).achunks()]

        self.path.write_bytes(b"abcdef")
        try:
            with mock.patch.object(body_module, "_open_binary", open_held):
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(read(), 0.05)
            self.assertTrue(gate.started.is_set())
            self.assertFalse(files[0].closed)
        finally:
            gate.release.set()
        for _ in range(500):
            if files[0].closed:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(files[0].closed)


class SaveBodyTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.req.body.save_body`
    """

    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "download.bin"

    def test_bytes_to_path(self) -> None:
        """
        Test that bytes are written to a newly created file and counted.
        """
        self.assertEqual(save_body(b"abc", self.path), 3)
        self.assertEqual(self.path.read_bytes(), b"abc")

    def test_path_is_truncated_by_default(self) -> None:
        """
        Test that an existing file is replaced, and that a path given as
        a string works as well.
        """
        self.path.write_bytes(b"old contents")
        self.assertEqual(save_body(IterableBody([b"ab", b"c"]), str(self.path)), 3)
        self.assertEqual(self.path.read_bytes(), b"abc")

    def test_append_to_path(self) -> None:
        """
        Test the resume case: with ``append=True`` the existing contents
        stay and the body is written behind them.
        """
        self.path.write_bytes(b"first, ")
        self.assertEqual(save_body(IterableBody([b"sec", b"ond"]), self.path, append=True), 6)
        self.assertEqual(self.path.read_bytes(), b"first, second")

    def test_file_object_is_written_in_place_and_left_open(self) -> None:
        """
        Test that a file object is written at its current position and
        not closed.
        """
        buffer = io.BytesIO(b"0123456789")
        buffer.seek(4)
        self.assertEqual(save_body(BytesBody(b"ab"), buffer), 2)
        self.assertFalse(buffer.closed)
        self.assertEqual(buffer.getvalue(), b"0123ab6789")

    def test_broken_stream_keeps_what_arrived(self) -> None:
        """
        Test that a body breaking off midway raises, with everything
        received until then in the file — the offset to resume from.
        """

        def broken() -> Iterator[bytes]:
            yield b"abc"
            yield b"def"
            raise ConnectionResetError("gone")

        with self.assertRaises(ConnectionResetError):
            save_body(IterableBody(broken()), self.path)
        self.assertEqual(self.path.read_bytes(), b"abcdef")

    def test_streamed_body_is_closed(self) -> None:
        """
        Test that saving consumes the body, which releases its resource.
        """
        closed: list[bool] = []
        body = IterableBody(iter([b"abc"]), close=lambda: closed.append(True))
        save_body(body, self.path)
        self.assertEqual(closed, [True])

    def test_unreadable_body_leaves_the_file_alone(self) -> None:
        """
        Test that a body which cannot deliver anything raises before the
        path is opened: an existing file keeps its contents, a missing
        one is not created.
        """

        async def source() -> AsyncIterator[bytes]:
            yield b"new"  # pragma: no cover

        def failing() -> Iterator[bytes]:
            raise ConnectionResetError("gone")
            yield b"new"  # pragma: no cover

        closed = SpooledBody()
        closed.close()
        # factories: a generator source fails only the first time it is read
        bodies: list[tuple[Callable[[], BodyProducer], type[Exception]]] = [
            (lambda: AsyncIterableBody(source()), RuntimeError),
            (lambda: closed, ValueError),
            (lambda: IterableBody(failing()), ConnectionResetError),
        ]
        for body, error in bodies:
            with self.subTest(error=error.__name__):
                self.path.write_bytes(b"precious")
                with self.assertRaises(error):
                    save_body(body(), self.path)
                self.assertEqual(self.path.read_bytes(), b"precious")
                self.path.unlink()
                with self.assertRaises(error):
                    save_body(body(), self.path)
                self.assertFalse(self.path.exists())

    def test_empty_body_still_replaces_the_file(self) -> None:
        """
        Test that a body which is readable but empty truncates (or
        creates) the file like any other.
        """
        for body in (b"", IterableBody([])):
            with self.subTest(body=type(body).__name__):
                self.path.write_bytes(b"old contents")
                self.assertEqual(save_body(body, self.path), 0)
                self.assertEqual(self.path.read_bytes(), b"")

    def test_short_writes_are_completed(self) -> None:
        """
        Test that a target taking only a part of the data per write — a
        raw file, a pipe — gets all of it nevertheless.
        """
        target = _RawTarget(accepts=3)
        written = save_body(IterableBody([b"abcdefgh", b"ij"]), cast("IO[bytes]", target))
        self.assertEqual(written, 10)
        self.assertEqual(target.data, b"abcdefghij")
        self.assertEqual(target.calls, 4)

    def test_write_returning_nothing_counts_as_complete(self) -> None:
        """
        Test that a file-like target whose write() returns ``None`` is
        written once per chunk.
        """
        target = _SilentTarget()
        self.assertEqual(save_body(IterableBody([b"abc", b"def"]), cast("IO[bytes]", target)), 6)
        self.assertEqual(target.data, b"abcdef")

    def test_stream_is_closed_when_the_write_fails(self) -> None:
        """
        Test that a body is not left half-read when the write fails: it
        is closed right away, which releases its resource.
        """
        released: list[bool] = []

        def stream() -> Iterator[bytes]:
            yield b"abc"
            yield b"def"

        class _FullDisk(io.BytesIO):
            def write(self, data: Any, /) -> int:
                raise OSError("no space left on device")

        body = IterableBody(stream(), close=lambda: released.append(True))
        with self.assertRaises(OSError):
            save_body(body, _FullDisk())
        self.assertEqual(released, [True])

    def test_non_blocking_raw_target_raises(self) -> None:
        """
        Test that a raw file answering ``None`` — not ready, nothing
        written — is not mistaken for a file-like object that returns
        nothing: the data would be lost silently.
        """
        with self.assertRaises(BlockingIOError) as raised:
            save_body(b"abc", cast("IO[bytes]", _NonBlockingTarget()))
        self.assertEqual(raised.exception.characters_written, 0)

    def test_target_accepting_nothing_raises(self) -> None:
        """
        Test that a target which takes no data at all raises instead of
        being retried forever.
        """
        target = _RawTarget(accepts=0)
        with self.assertRaises(OSError):
            save_body(b"abc", cast("IO[bytes]", target))
        self.assertEqual(target.calls, 1)


class AsyncSaveBodyTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for :py:func:`action0.req.body.asave_body`
    """

    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "download.bin"

    async def test_bytes_to_path(self) -> None:
        """
        Test that bytes are written to a newly created file, with every
        file operation in a worker thread.
        """
        with _recorded_thread_hops() as hops:
            self.assertEqual(await asave_body(b"abc", self.path), 3)
        self.assertEqual(self.path.read_bytes(), b"abc")
        self.assertEqual(hops, ["_open_and_write", "close"])

    async def test_append_to_path(self) -> None:
        """
        Test the resume case for an async stream.
        """

        async def source() -> AsyncIterator[bytes]:
            yield b"sec"
            yield b"ond"

        self.path.write_bytes(b"first, ")
        written = await asave_body(AsyncIterableBody(source()), self.path, append=True)
        self.assertEqual(written, 6)
        self.assertEqual(self.path.read_bytes(), b"first, second")

    async def test_file_object_is_left_open(self) -> None:
        """
        Test that a file object is written at its position and not
        closed.
        """
        buffer = io.BytesIO(b"0123456789")
        buffer.seek(4)
        self.assertEqual(await asave_body(IterableBody([b"a", b"b"]), buffer), 2)
        self.assertFalse(buffer.closed)
        self.assertEqual(buffer.getvalue(), b"0123ab6789")

    async def test_small_chunks_are_batched(self) -> None:
        """
        Test that many small chunks cost few thread hops: they are
        written 64 KiB at a time, the rest at the end.
        """
        chunk = b"x" * 10240
        with _recorded_thread_hops() as hops:
            written = await asave_body(IterableBody([chunk] * 10), self.path)
        self.assertEqual(written, 102400)
        self.assertEqual(self.path.read_bytes(), chunk * 10)
        # the first chunk is written along with the open; of the others
        # 7 reach the 64 KiB batch size and 2 follow at the end
        self.assertEqual(hops, ["_open_and_write", "write_pending", "write_pending", "close"])

    async def test_broken_stream_keeps_what_arrived(self) -> None:
        """
        Test that a body breaking off midway raises, with the chunks
        still waiting in the batch written out first.
        """

        async def broken() -> AsyncIterator[bytes]:
            yield b"abc"
            yield b"def"
            raise ConnectionResetError("gone")

        with self.assertRaises(ConnectionResetError):
            await asave_body(AsyncIterableBody(broken()), self.path)
        self.assertEqual(self.path.read_bytes(), b"abcdef")

    async def test_failed_write_is_not_repeated(self) -> None:
        """
        Test that a batch whose write failed is not written a second
        time on the way out.
        """

        class _FullDisk(io.BytesIO):
            attempts = 0

            def write(self, data: Any) -> int:
                type(self).attempts += 1
                raise OSError("no space left on device")

        target = _FullDisk()
        with self.assertRaises(OSError):
            await asave_body(IterableBody([b"x" * 70000, b"y"]), target)
        self.assertEqual(_FullDisk.attempts, 1)

    async def test_unreadable_body_leaves_the_file_alone(self) -> None:
        """
        Test that a body which cannot deliver anything raises before the
        path is opened: an existing file keeps its contents, a missing
        one is not created.
        """

        async def failing() -> AsyncIterator[bytes]:
            raise ConnectionResetError("gone")
            yield b"new"  # pragma: no cover

        closed = SpooledBody()
        closed.close()
        bodies: list[tuple[BodyProducer, type[Exception]]] = [
            (closed, ValueError),
            (AsyncIterableBody(failing()), ConnectionResetError),
        ]
        for body, error in bodies:
            with self.subTest(body=type(body).__name__):
                self.path.write_bytes(b"precious")
                with _recorded_thread_hops() as hops, self.assertRaises(error):
                    await asave_body(body, self.path)
                self.assertEqual(hops, [])
                self.assertEqual(self.path.read_bytes(), b"precious")

    async def test_empty_body_still_replaces_the_file(self) -> None:
        """
        Test that a body which is readable but empty truncates (or
        creates) the file like any other.
        """
        for body in (b"", IterableBody([])):
            with self.subTest(body=type(body).__name__):
                self.path.write_bytes(b"old contents")
                self.assertEqual(await asave_body(body, self.path), 0)
                self.assertEqual(self.path.read_bytes(), b"")

    async def test_short_writes_are_completed(self) -> None:
        """
        Test that a target taking only a part of the data per write gets
        all of it, across the batches.
        """
        target = _RawTarget(accepts=3)
        written = await asave_body(IterableBody([b"abcdefgh", b"ij"]), cast("IO[bytes]", target))
        self.assertEqual(written, 10)
        self.assertEqual(target.data, b"abcdefghij")

    async def test_stream_is_closed_when_the_write_fails(self) -> None:
        """
        Test that a body is not left half-read when the write fails: it
        is closed right away, which releases its resource.
        """
        released: list[bool] = []

        async def stream() -> AsyncIterator[bytes]:
            yield b"x" * 70000
            yield b"more"

        async def release() -> None:
            released.append(True)

        class _FullDisk(io.BytesIO):
            def write(self, data: Any, /) -> int:
                raise OSError("no space left on device")

        body = AsyncIterableBody(stream(), aclose=release)
        with self.assertRaises(OSError):
            await asave_body(body, _FullDisk())
        self.assertEqual(released, [True])

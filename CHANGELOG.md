# Changelog

All notable changes to action0-req are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the version
numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- `SpooledBody`: a body producer that is written first and read
  afterwards. It keeps the data in memory up to `max_memory` bytes and
  moves it to an anonymous temporary file beyond that. It is re-readable,
  also by several consumers at once, offers read-only, seekable file
  views via `file()`, and is removed deterministically by `close()` or a
  `with` block. `awrite()`, `achunks()` and `aclose()` do their file I/O
  in a worker thread.
- `Response.body_file()`: the response body as a seekable binary file
  object, whatever form the body has. A streamed body is spooled, and
  the stream closed; a `FileBody` hands out its file without a copy
  (`FileBody.close()` closes a file opened for that).
- `Response.save_body()` and `Response.asave_body()`, plus the functions
  `save_body()` and `asave_body()`: copy a body into a path or an open
  file object chunk by chunk, with `append=True` for resumed downloads.
  A path is opened only once the body has delivered its first chunk, so
  a body that cannot be read leaves an existing file untouched. A write
  that fails closes the stream.
- `Response.close()` and `Response.aclose()`; a response is now a sync
  and async context manager. They release what the body holds and do
  nothing for in-memory bodies. When a `with` block is left by an
  exception, a failure to close is attached to that exception as a note
  instead of replacing it.
- `IterableBody(..., close=...)` and `AsyncIterableBody(..., aclose=...)`:
  a callback releasing the resource behind the stream. It runs exactly
  once: after the body has been consumed, when a consumer stops early,
  or on `close()` / `aclose()`. Both classes are context managers.
  Closing also closes a generator behind the body, so one that was read
  in part runs its `finally` blocks right away.
  `AsyncIterableBody.close()` is the synchronous counterpart for a body
  with nothing left to release; it raises while the callback is pending.
- trio support for asynchronous file I/O: `FileBody.achunks()` and the
  new asynchronous file operations use trio's thread pool when running
  below trio. Before, `FileBody.achunks()` worked on asyncio only.
  Cleanup — closing a file, writing out what has arrived — also runs
  when the task has been cancelled.

### Fixed

- `FileBody.achunks()` no longer leaves the file it opened unclosed when
  the consuming task is cancelled: the close also runs when the task is
  cancelled repeatedly, as anyio does on a timeout.
- `FileBody.achunks()` on asyncio: a cancelled read of a regular file or
  a seekable file object is finished before the cancellation is passed
  on, so it no longer moves the file position under the next reader. A
  source that may block for good (a named pipe, a device, a non-seekable
  file object) is still left behind at once — now in a daemon thread of
  its own, which neither occupies the loop's thread pool nor delays its
  shutdown, and which closes the file it opened.

## 0.1.1 — 2026-08-08

### Changed

- Installation instructions: the package is published on PyPI.

## 0.1.0 — 2026-08-08

First release: the `Header`, `Status` and `Method` constants, `Headers`,
`Request`, `Response` and the `BodyProducer` protocol with `BytesBody`,
`FileBody`, `IterableBody` and `AsyncIterableBody`.

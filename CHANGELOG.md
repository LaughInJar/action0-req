# Changelog

All notable changes to action0-req are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the version
numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- `SpooledBody`: a body producer that is written first and read
  afterwards. It keeps the data in memory up to `max_memory` bytes and
  moves it to an anonymous temporary file beyond that. It is re-readable,
  offers a seekable binary file object via `file()`, and is removed
  deterministically by `close()` or a `with` block. `awrite()`,
  `achunks()` and `aclose()` do their file I/O in a worker thread.
- `Response.body_file()`: the response body as a seekable binary file
  object, whatever form the body has. A streamed body is spooled.
- `Response.save_body()` and `Response.asave_body()`, plus the functions
  `save_body()` and `asave_body()`: copy a body into a path or an open
  file object chunk by chunk, with `append=True` for resumed downloads.
- `Response.close()` and `Response.aclose()`; a response is now a sync
  and async context manager. They release what the body holds and do
  nothing for in-memory bodies.
- `IterableBody(..., close=...)` and `AsyncIterableBody(..., aclose=...)`:
  a callback releasing the resource behind the stream. It runs exactly
  once: after the body has been consumed, when a consumer stops early,
  or on `close()` / `aclose()`. Both classes are context managers.
- trio support for asynchronous file I/O: `FileBody.achunks()` and the
  new asynchronous file operations use trio's thread pool when running
  below trio. Before, `FileBody.achunks()` worked on asyncio only.

## 0.1.1 — 2026-08-08

### Changed

- Installation instructions: the package is published on PyPI.

## 0.1.0 — 2026-08-08

First release: the `Header`, `Status` and `Method` constants, `Headers`,
`Request`, `Response` and the `BodyProducer` protocol with `BytesBody`,
`FileBody`, `IterableBody` and `AsyncIterableBody`.

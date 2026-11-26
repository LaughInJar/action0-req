# Changelog

All notable changes to action0-req are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the version
numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Changed

- **action0-url 0.2.0 is required** (was 0.1.0). Its changes show through
  wherever a request has a URL; see the
  [action0-url changelog](https://github.com/LaughInJar/action0-url/blob/main/CHANGELOG.md)
  for all of them. What you will notice in requests:
  - **No default scheme.** `Request("example.com/items")` used to become
    `https:example.com/items`; now it is the relative path it is written
    as. Write the scheme: `Request("https://example.com/items")`.
  - **The URL is kept as it is written.** Percent escapes (`%2F`, `%7E`,
    escapes that are not valid UTF-8) and the spelling of the query
    (`?flag`, `%20`) are no longer decoded and re-encoded, so
    `request.url.as_str()` and `repr(request)` show a signed URL
    unchanged.
  - **Requests are equal when their URLs are equivalent:** `%7E` equals
    `~`, but `/a%2Fb` no longer equals `/a/b`.
  - **Query parameter values** have to be strings, ints, floats or bools
    (or an iterable of those): bytes, a `Decimal` or a `date` in
    `query=` raise a `TypeError` — convert them with `str()`.
- `Request.as_str()` always writes an absolute path as target: a request
  with a relative path and no host (`Request("items/5")`) is rendered as
  `GET /items/5`, was `GET items/5`.
- `Request(url, query=None)` keeps the query of the URL, as before. This
  now differs from `Url(url, query=None)`, which removes it since
  action0-url 0.2.0; pass an empty mapping to remove the query.

### Fixed

Earlier releases of action0-req accept action0-url 0.2.0 as well, and
these show up with it:

- `Request.as_str()` wrote a request target that differed from the URL
  of the request, because it was rebuilt from the decoded parts: an
  escaped `/` became a real one (`/a%2Fb` → `/a/b`), `%7E` became `~`,
  an escape that is not valid UTF-8 became `%EF%BF%BD`, and for schemes
  other than `http`, `https`, `ws` and `wss` a `+` in the query was
  written as `%2B`. The target is now the URL's own spelling of path,
  path params and query, character for character.
- `Request(url, query="a+b")` read the `+` of a query string as a space
  for every scheme; it is read like the `Url` constructor reads it now
  (as a space only for `http`, `https`, `ws`, `wss` and URLs without a
  scheme).

Independent of the action0-url version:

- A path with an empty first segment (`https://example.com//a`) got a
  wrong target in `Request.as_str()`: `////a` with action0-url 0.1.0,
  `/.//a` with 0.2.0. It is `//a` now.
- The `Host` header derived by `Request.as_str()` has the hostname as it
  is written in the URL string: a non-ASCII hostname is IDNA-encoded
  (`Host: xn--bcher-kva.example`, was `bücher.example`). With action0-url
  0.2.0 an IPv6 address also keeps its brackets (`Host: [::1]:8080`, was
  `::1:8080`).

## 0.2.0 — 2026-11-26

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

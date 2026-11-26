# Changelog

All notable changes to action0-req are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the version
numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- `Request.target`: the request target to render instead of the
  origin-form derived from the URL — `"*"` for a server-wide `OPTIONS`,
  `"host:port"` for `CONNECT`, an absolute URL for a request to a proxy.
  Also a constructor keyword; part of `copy()` and of equality.
- `Request.from_str()` and `Response.from_str()`: parse the text that
  `as_str()` renders — start line, header lines and, behind a blank
  line, the body (as a `str`). The request URL is put together from the
  target and the `Host` header, with the `scheme` you pass.
- `abody_bytes()` and `abody_str()` on `Request` and `Response`, and
  `Response.abody_file()`: the asynchronous twins of the body accessors.
  They read a producer through `achunks()`, which makes an
  `AsyncIterableBody` readable as a whole and as a file, and keeps a
  file-backed body from blocking the event loop.
- `Response.body_file()` takes `max_memory` and `dir` for the
  `SpooledBody` a stream is read into.
- `Request.close()` and `Request.aclose()`; a request is a sync and
  async context manager like a response, for a body that holds a
  resource and was never sent.
- `IterableBody(..., threaded=True)`: `achunks()` fetches every chunk in
  a worker thread, for an iterable that blocks while it produces one.
  Without it the iterable runs on the event loop, as before — which is
  now documented.
- `Request.secret_params`: the names of the query parameters whose
  values `repr(request)` shows as `***` (`api_key`, `token`,
  `signature`, …), overridable like `Headers.secret_names`.
- `BytesBody` compares by its bytes (and is hashable), so requests and
  responses with equal `BytesBody` bodies are equal. All producers have
  a `repr()` that says what they are without showing their data.
- `BodyTypes`, `HeaderTypes` and `HeaderValue` are importable from the
  package root.
- `Header.CONNECT_UDP_BIND`, `Header.NO_VARY_SEARCH` and
  `Header.PROXY_PUBLIC_ADDRESS`: the field names registered since the
  last snapshot of the IANA registry (now as of 2026-11-26).

### Changed

Behaviour that code may rely on:

- **A streamed body can be read once, and says so.** Reading an
  `IterableBody` or `AsyncIterableBody` over a generator (or any other
  iterator) a second time, or after it has been closed, raises a
  `RuntimeError` explaining what happened. It used to return nothing,
  which passed for an empty body — `Response.save_body()` after
  `body_bytes()` truncated the file. A read that is between two chunks
  when the body is closed raises as well instead of ending quietly.
  Bodies over a list are re-readable as before; for a response,
  `body_file()` / `abody_file()` keep a stream readable.
- **Header names and values must not contain a CR, an LF or a NUL**:
  `Headers` raises a `ValueError` for them, whichever way they come in.
  `Request.as_str()` and `Response.as_str()` do the same for the method,
  the target, the HTTP version and the reason phrase.
- **A raw header block is split at `\r\n` and `\n` only** (it was
  `str.splitlines()`, which also splits at `\x0b`, `\x0c`, `\x85`,
  U+2028, … and a lone `\r`), and a line whose name contains whitespace
  — `Name : value`, a folded line, a request line — is a `ValueError`.
- **Bytes are not header values**: `Headers({"X": b"ab"})` raises a
  `TypeError` (it produced the lines `X: 97` and `X: 98`), and so does a
  string where a name/value tuple is expected (`Headers(["ab"])`).
- **`Response.as_str()` keeps the space behind the status code** when
  there is no phrase: `"HTTP/1.1 599 "`, as the grammar of the status
  line requires (was `"HTTP/1.1 599"`).
- **`Response.status` must be an int**: anything else raises a
  `TypeError` when it is set.
- **`FileBody.content_length()` is `None` for a path that is not a
  regular file** (a named pipe, a device); it was the `0` of the
  directory entry.
- `Request.method` is uppercased whenever it is set, not only by the
  constructor.
- `repr(request)` shows the values of the `Request.secret_params` query
  parameters as `***`.
- `Headers.get_values()` no longer splits at a comma inside a quoted
  string (`private="a, b"`).
- `Headers.copy()` returns an instance of the same class and keeps a
  `secret_names` set on the instance; `Request`/`Response` keep a
  `Headers` subclass they are given, and their `copy()` returns an
  instance of their own class.
- A `SpooledBody` whose data cannot be moved to the temporary file is
  closed (see Fixed).

And from the dependency:

- **action0-url 0.2.0 is required** (was 0.1.0), and the requirement is
  capped below 0.3: a minor release of a 0.x package may change
  behaviour, as this one did. Its changes show through
  wherever a request has a URL; see the
  [action0-url changelog](https://github.com/LaughInJar/action0-url/blob/main/CHANGELOG.md)
  for all of them. What you will notice in requests:
  - **No default scheme.** `Request("example.com/items")` used to become
    `https:example.com/items`; now it is the relative path it is written
    as. Write the scheme: `Request("https://example.com/items")`.
  - **The URL is kept as it is written.** Percent escapes (`%2F`, `%7E`,
    escapes that are not valid UTF-8) and the spelling of the query
    (`?flag`, `%20`) are no longer decoded and re-encoded, so
    `request.url.as_str()` shows a signed URL unchanged — and
    `repr(request)` does too, apart from the values it redacts.
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

- `as_str(include_body=True)` raised for a body that is not text in the
  Content-Type charset (a compressed body: `UnicodeDecodeError`) or
  whose charset Python does not know (`LookupError`). It is a rendering
  and shows such bytes with U+FFFD replacement characters now; the
  accessors `body_str()` / `body_bytes()` raise as before, which is
  documented.
- A custom redaction was lost on the way into a request or response:
  `Headers.copy()` and the constructors rebuilt a plain `Headers`, so a
  subclass or an instance with more `secret_names` printed its secrets
  in `repr()`.
- The asynchronous file operations could be left behind by a shutdown:
  cancelling all tasks of the loop (`asyncio.run()` on its way out, a
  signal handler) also cancelled the task the worker thread was wrapped
  in, and `asave_body()` closed its file while a write was still
  running. The call is a plain future now.
- A `SpooledBody` whose rollover to the temporary file failed midway (a
  full disk) kept answering with a part of the data — `content_length()`
  said one thing, `as_bytes()` returned another — and the next write
  raised an `AttributeError`. The body is closed now and the error
  passes through.
- `Headers() == {"a": None}` (and other mappings that cannot be headers)
  raised instead of being unequal.
- Reading a named pipe or a non-seekable file object with
  `FileBody.achunks()` on asyncio started a thread per chunk; the daemon
  threads are reused now.

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

# action0-req

Typed Python representations of HTTP requests and responses with
convenient creation, duplication and manipulation — the companion of
[action0-url](https://laughinjar.github.io/action0-url/), which provides
the URL representation.

```shell
pip install action0-req  # or uv add action0-req
```

**Highlights**:

- {py:class}`~action0.req.headers.Header`,
  {py:class}`~action0.req.status.Status` and
  {py:class}`~action0.req.request.Method` — `StrEnum`/`IntEnum` constants
  for the IANA-registered header field names and status codes (with
  reason phrases and category properties) and for the request methods of
  RFC 9110, plus `PATCH` and `QUERY`.
- {py:class}`~action0.req.headers.Headers` — an ordered,
  case-insensitive, multi-value aware mapping of HTTP header fields that
  preserves the representation exactly, redacts secret values in
  `repr()` and refuses names and values that would inject a line.
- {py:class}`~action0.req.request.Request` — method, URL, headers, body
  and HTTP version as plain attributes, with typed body accessors
  (`body_bytes()` / `body_str()` / `body_producer()`, and their
  asynchronous twins), `copy()` with overrides, a wire-format `as_str()`
  and its inverse `from_str()`.
- {py:class}`~action0.req.response.Response` — status (with reason
  phrase fallback and category properties), headers, body and an
  optional back-reference to the request, mirroring `Request`.
- Streaming of request and response bodies via the
  {py:class}`~action0.req.body.BodyProducer` protocol — sync and async
  (without blocking the event loop), with in-memory, file-backed and
  (async-)iterable-backed producers.
- Response bodies as files: spooled to a temporary file while they
  arrive ({py:class}`~action0.req.body.SpooledBody`), read through a
  seekable file object, saved to a path — and released
  deterministically with `close()` or a `with` block.
- Fully typed (checked with mypy strict, pyright and ty), Python 3.11+,
  no runtime dependencies beyond `action0-url`.

The `action0` namespace is simply the one the author likes to use for
personal projects.

```{toctree}
:maxdepth: 2

usage
api
changelog
```

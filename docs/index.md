# action0-req

Typed Python representations of HTTP requests and responses with
convenient creation, duplication and manipulation — the companion of
[action0-url](https://laughinjar.github.io/action0-url/), which provides
the URL representation.

```shell
uv add action0-req    # not on PyPI yet — install from GitHub for now
```

**Highlights** (the library is under construction — the class layer is
still to come):

- {py:class}`~action0.req.headers.Header`,
  {py:class}`~action0.req.status.Status` and
  {py:class}`~action0.req.request.Method` — `StrEnum`/`IntEnum` constants
  for the IANA-registered header field names, status codes (with reason
  phrases and category properties) and request methods.
- `Headers` — a case-insensitive, multi-value aware mapping of HTTP
  header fields *(planned)*.
- `Request` / `Response` — every part (method, URL, status, headers,
  body) a plain attribute; parse once, change what you need *(planned)*.
- Optional streaming of request and response bodies — sync and async,
  convenience and performance focused *(planned)*.
- Fully typed (checked with mypy strict, pyright and ty), Python 3.11+,
  no runtime dependencies beyond `action0-url`.

The `action0` namespace is simply the one the author likes to use for
personal projects.

```{toctree}
:maxdepth: 2

usage
api
```

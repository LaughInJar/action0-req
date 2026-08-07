# action0-req

Typed Python representations of HTTP requests and responses with
convenient creation, duplication and manipulation — the companion of
[action0-url](https://laughinjar.github.io/action0-url/), which provides
the URL representation.

```shell
uv add action0-req    # not on PyPI yet — install from GitHub for now
```

**Planned highlights** (the library is under construction):

- `Headers` — a case-insensitive, multi-value aware mapping of HTTP
  header fields, plus constants for the common header names.
- `Request` / `Response` — every part (method, URL, status, headers,
  body) a plain attribute; parse once, change what you need.
- Constants for the known HTTP status codes and common headers.
- Optional streaming of request and response bodies — sync and async,
  convenience and performance focused.
- Fully typed (checked with mypy strict, pyright and ty), Python 3.11+,
  no runtime dependencies beyond `action0-url`.

The `action0` namespace is simply the one the author likes to use for
personal projects.

```{toctree}
:maxdepth: 2

usage
api
```

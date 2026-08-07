# Action0-Req

[![CI](https://github.com/LaughInJar/action0-req/actions/workflows/ci.yml/badge.svg)](https://github.com/LaughInJar/action0-req/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/action0-req)](https://pypi.org/project/action0-req/)

Typed Python representations of HTTP requests and responses with
convenient creation, duplication and manipulation. The companion of
[action0-url](https://github.com/LaughInJar/action0-url), which provides
the URL representation.

Requires Python 3.11 or newer.

Full documentation including the API reference:
<https://laughinjar.github.io/action0-req/>

**Status:** the core API is complete — the header name, status code and
method constants, the `Headers` mapping, `Request` and `Response`, and
sync/async body streaming via `BodyProducer` (`BytesBody`, `FileBody`,
`IterableBody`, `AsyncIterableBody`).

## Usage

`Header`, `Status` and `Method` are `StrEnum`/`IntEnum` constants for the
IANA-registered header field names, status codes and request methods —
every member *is* its string or integer value:

```python
from action0.req import Header, Method, Status

print(Header.CONTENT_TYPE)  # Content-Type
print(Method.POST)  # POST
print(Status.NOT_FOUND)  # 404
print(Status.NOT_FOUND.phrase)  # Not Found
print(Status.NOT_FOUND.is_client_error)  # True
```

`Headers` is an ordered, case-insensitive, multi-value aware mapping of
header fields that preserves the representation (order and casing)
exactly:

```python
from action0.req import Headers

headers = Headers({"Content-Type": "text/html"})
headers.add("Set-Cookie", "a=1")
headers.add("set-cookie", "b=2")
print(headers["CONTENT-TYPE"])  # text/html
print(headers.get_all("Set-Cookie"))  # ['a=1', 'b=2']
print(headers.as_str())  # Content-Type: text/html\r\nSet-Cookie: a=1\r\nset-cookie: b=2

# repr()/str() redact secrets, only as_str() renders them
print(Headers({"Authorization": "Bearer tok"}))  # Headers(Authorization: ***)
```

`Request` combines a method, a `Url` (from action0-url), `Headers` and a
body (`bytes`, `str` or a streaming `BodyProducer` — retrievable in any
of the three forms):

```python
from action0.req import Request

req = Request("https://api.example.com/items", query={"page": 2})
req.headers["Accept"] = "application/json"
print(req.as_str())
# GET /items?page=2 HTTP/1.1\r\nHost: api.example.com\r\nAccept: application/json

post = req.copy(method="POST", body='{"a": 1}')
print(post.body_bytes())  # b'{"a": 1}'
print(post)  # Request(POST https://api.example.com/items?page=2)
```

`Response` mirrors it for the server side — status (with reason phrase
fallback and category properties), headers, body:

```python
from action0.req import Response, Status

resp = Response(Status.NOT_FOUND, headers={"Content-Type": "text/plain"}, body="not here")
print(resp.phrase, resp.is_client_error)  # Not Found True
print(resp.as_str())
# HTTP/1.1 404 Not Found\r\nContent-Type: text/plain
```

## Installation

Not published to PyPI yet; install straight from GitHub:

```shell
uv add "action0-req @ git+https://github.com/LaughInJar/action0-req"
```

## Development

The project is managed with [uv](https://docs.astral.sh/uv/); `uv run`
creates and syncs the virtual environment automatically:

```shell
uv run pytest        # run the tests (incl. the docstring examples as doctests)
uv run ruff check    # lint
uv run ruff format   # format
uv run mypy          # type-check (also: uv run pyright, uv run ty check)

# build the docs (Sphinx; deployed to GitHub Pages on push to main)
uv run --group docs sphinx-build -W docs docs/_build/html
```

### Releasing

The version lives only in `src/action0/req/__init__.py` (`__version__`).
To release: bump it, merge to `main`, then tag the release commit and push
the tag — the release workflow re-runs all checks, verifies the tag
matches `__version__`, builds sdist + wheel and publishes to PyPI via
trusted publishing:

```shell
git tag v0.1.0
git push origin v0.1.0
```

## About action0

This is just the namespace I like to use for my personal projects.
I quite like namespaces.

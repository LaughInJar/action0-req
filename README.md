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

**Status: under construction.** The `Headers` mapping and the header
name, status code and method constants exist; still planned:

- `action0.req.request.Request` and `action0.req.response.Response` —
  every part (method, URL, status, headers, body) a plain attribute.
- Optional streaming of request and response bodies — sync and async,
  convenience and performance focused.

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

# Guide

Everything here is runnable as-is; the `#` comments show the exact output.

## Installation

`action0-req` is not published to PyPI yet; install it straight from
GitHub:

```shell
uv add "action0-req @ git+https://github.com/LaughInJar/action0-req"
```

## Constants

The header names, status codes and request methods all exist as enum
constants importable from the package root. The enums subclass
{py:class}`enum.StrEnum` / {py:class}`enum.IntEnum`, so every member *is*
its string or integer value:

```python
from action0.req import Header, Method, Status

print(Header.CONTENT_TYPE)
# Content-Type
print(Method.POST)
# POST
print(Status.NOT_FOUND)
# 404
```

{py:class}`~action0.req.status.Status` members carry their registered
reason phrase and know their category:

```python
from action0.req import Status

print(Status.NOT_FOUND.phrase)
# Not Found
print(Status(301).phrase)
# Moved Permanently
print(Status.NOT_FOUND.is_client_error, Status.NOT_FOUND.is_server_error)
# True False
```

{py:class}`~action0.req.headers.Header` covers the full [IANA field name
registry](https://www.iana.org/assignments/http-fields/) (the permanent,
deprecated and provisional registrations) plus a handful of widely used
unregistered `X-…` names:

```python
from action0.req import Header

print(Header.WWW_AUTHENTICATE)
# WWW-Authenticate
print(Header.X_FORWARDED_FOR)
# X-Forwarded-For
print(Header("ETag") is Header.ETAG)
# True
```

## Working with headers

{py:class}`~action0.req.headers.Headers` is an ordered, case-insensitive,
multi-value aware mapping of HTTP header fields. It behaves like a dict
(a `MutableMapping`) where subscription works with a single value per
field — the last line's — while `add()`, `get_all()` and friends handle
multiple lines. Lookup is case-insensitive per RFC 9110, but the order
and casing of the representation are preserved exactly:

```python
from action0.req import Headers

headers = Headers({"Content-Type": "text/html"})
headers.add("Set-Cookie", "a=1")
headers.add("set-cookie", "b=2")
print(headers["CONTENT-TYPE"])
# text/html
print(headers.get_all("Set-Cookie"))
# ['a=1', 'b=2']
print(len(headers), "SET-COOKIE" in headers)
# 2 True
print(headers.as_str(separator="\n"))
# Content-Type: text/html
# Set-Cookie: a=1
# set-cookie: b=2
```

Names are never normalized beyond casing — an underscore does not match
a dash (`headers["content_type"]` would *not* find `Content-Type`); use
the {py:class}`~action0.req.headers.Header` constants instead of magic.

Headers can be created from (and merged with) a raw header block, a
mapping, a list of name/value tuples or another instance; non-string
values are coerced. `update()` replaces existing fields in place and
appends new ones:

```python
from action0.req import Headers

headers = Headers("Host: example.com\nAccept: text/html")
headers.update({"Accept": "application/json", "Content-Length": 42})
print(headers.as_str(separator="\n"))
# Host: example.com
# Accept: application/json
# Content-Length: 42
```

`get_values()` splits the lines into their comma-separated elements
(RFC 9110 list syntax — don't use it for `Set-Cookie`, whose values can
contain literal commas):

```python
from action0.req import Headers

headers = Headers("Vary: Accept-Encoding, Accept-Language\nVary: Cookie")
print(headers.get_values("vary"))
# ['Accept-Encoding', 'Accept-Language', 'Cookie']
```

Equality ignores the order of different fields and the casing of names,
but respects the order of a field's own lines. `repr()` and `str()`
redact the values of secret fields (`Authorization`, cookies, … — the
overridable `Headers.secret_names` set); only the wire rendering
`as_str()` keeps them:

```python
from action0.req import Headers

print(Headers("A: 1\nB: 2") == Headers("b: 2\nA: 1"))
# True
headers = Headers({"Authorization": "Bearer secret", "Accept": "*/*"})
print(headers)
# Headers(Authorization: ***, Accept: */*)
print(headers.as_str(separator="\n"))
# Authorization: Bearer secret
# Accept: */*
```

## Requests

{py:class}`~action0.req.request.Request` represents an HTTP request:
method, URL, headers, body and HTTP version — every part a plain mutable
attribute (the URL a {py:class}`~action0.url.base.Url` from
[action0-url](https://laughinjar.github.io/action0-url/), the headers a
{py:class}`~action0.req.headers.Headers`). The URL can be passed as a
string or an existing `Url` (which is copied); `query` replaces the URL's
query like the `Url` constructor does; the method is uppercased:

```python
from action0.req import Request

req = Request("https://api.example.com/items", query={"page": 2})
req.headers["Accept"] = "application/json"
print(req.method, req.url)
# GET https://api.example.com/items?page=2
print(req.as_str(separator="\n"))
# GET /items?page=2 HTTP/1.1
# Host: api.example.com
# Accept: application/json
```

`as_str()` renders the wire format: the request line with the origin-form
target and the headers, deriving `Host` from the URL when none is set
(like `Headers.as_str()` it does NOT redact secret values — `repr()`
does). `copy()` returns an independent request, with keyword overrides
for any constructor argument:

```python
from action0.req import Request

req = Request("https://user:secret@api.example.com/items")
clone = req.copy(method="POST", query={"id": 7})
print(clone)
# Request(POST https://user:***@api.example.com/items?id=7)
```

### Request bodies

The body can be set as `bytes`, `str` or a streaming
{py:class}`~action0.req.body.BodyProducer` — and retrieved in any of the
three forms, regardless of how it was set. Text is encoded/decoded with
the `Content-Type` charset (utf-8 when there is none):

```python
from action0.req import Request

req = Request("https://api.example.com/items", "POST", body='{"a": 1}')
print(req.body_str())
# {"a": 1}
print(req.body_bytes())
# b'{"a": 1}'
print(req.body_producer().content_length())
# 8
print(req.as_str(include_body=True, separator="\n"))
# POST /items HTTP/1.1
# Host: api.example.com
#
# {"a": 1}
```

A `BodyProducer` body is streamed in chunks — synchronously via
`chunks()` or asynchronously via `achunks()`; `as_str(include_body=True)`
never consumes it and shows a placeholder instead. `BytesBody` is the
in-memory implementation:

```python
from action0.req import BytesBody, Request

req = Request("https://api.example.com/upload", "PUT", body=BytesBody(b"data"))
print(list(req.body_producer().chunks()))
# [b'data']
print(req.as_str(include_body=True, separator="\n"))
# PUT /upload HTTP/1.1
# Host: api.example.com
#
# <BytesBody>
```

## Streaming bodies

Besides {py:class}`~action0.req.body.BytesBody` there are three
streaming producers. {py:class}`~action0.req.body.FileBody` streams a
file in chunks; given a path it opens the file freshly per iteration, so
the body is re-iterable (a seekable open file object works too and is
rewound per iteration; a non-seekable one is consumed once):

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from action0.req import FileBody

with TemporaryDirectory() as tmp:
    path = Path(tmp) / "upload.bin"
    path.write_bytes(b"abcdef")
    body = FileBody(path, chunk_size=4)
    print(body.content_length())
    # 6
    print(list(body.chunks()))
    # [b'abcd', b'ef']
    print(list(body.chunks()))  # re-iterable
    # [b'abcd', b'ef']
```

{py:class}`~action0.req.body.IterableBody` wraps any iterable of byte
chunks — e.g. a generator (then single-use); the length is unknown, such
a body would be sent chunked:

```python
from action0.req import IterableBody, Request


def generate():
    yield b"chunk1"
    yield b"chunk2"


req = Request("https://api.example.com/upload", "PUT", body=IterableBody(generate()))
print(req.body_producer().content_length())
# None
print(req.body_bytes())  # reads the stream — consumes a generator source
# b'chunk1chunk2'
```

{py:class}`~action0.req.body.AsyncIterableBody` wraps an asynchronous
iterable — e.g. an async generator proxying another stream. It is
async-only: the synchronous `chunks()`/`as_bytes()` raise a
`RuntimeError`. Every producer supports `achunks()` for asynchronous
consumption (`FileBody` runs its file operations in the default thread
pool, so the event loop is never blocked):

```python
import asyncio

from action0.req import AsyncIterableBody


async def generate():
    yield b"async"
    yield b"chunks"


async def main():
    return [chunk async for chunk in AsyncIterableBody(generate()).achunks()]


print(asyncio.run(main()))
# [b'async', b'chunks']
```

## Responses

{py:class}`~action0.req.response.Response` mirrors `Request` for the
server side: status, headers, body and HTTP version as plain attributes,
with the same three body accessors. The status takes any int —
{py:class}`~action0.req.status.Status` members included — and the
{py:attr}`~action0.req.response.Response.phrase` falls back to the
registry when the server didn't send its own reason:

```python
from action0.req import Response, Status

resp = Response(Status.NOT_FOUND, headers={"Content-Type": "text/plain"}, body="not here")
print(resp.status, resp.phrase)
# 404 Not Found
print(resp.is_client_error, resp.is_success)
# True False
print(resp.as_str(include_body=True, separator="\n"))
# HTTP/1.1 404 Not Found
# Content-Type: text/plain
#
# not here
```

An explicitly set reason wins over the registry, and unregistered codes
simply have no phrase (the category properties still work for them):

```python
from action0.req import Response

print(Response(404, reason="Nope"))
# Response(404 Nope)
print(Response(599).as_str())
# HTTP/1.1 599
print(Response(599).is_server_error)
# True
```

A response can reference the request that produced it — metadata that is
shared, not copied, and ignored by equality:

```python
from action0.req import Request, Response

req = Request("https://api.example.com/items")
resp = Response(200, request=req)
print(resp.request)
# Request(GET https://api.example.com/items)
print(resp == Response(200))
# True
```

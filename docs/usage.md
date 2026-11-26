# Guide

Everything here is runnable as-is; the `#` comments show the exact output.

## Installation

```shell
pip install action0-req  # or uv add action0-req
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
And casing means the ASCII letters, as on the wire: a lookalike from
outside ASCII is a field of its own, not the header it resembles.

```python
from action0.req import Headers

headers = Headers([("Hoſt", "evil.example"), ("HOST", "example.com")])
print(headers["host"], len(headers))
# example.com 2
```

Headers can be created from (and merged with) a raw header block, a
mapping, a list of name/value tuples or another instance; numbers and
bools are coerced to strings. `update()` replaces existing fields in
place and appends new ones:

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
(RFC 9110 list syntax); a comma inside a quoted string is not a
separator. Use it only for fields that are lists: `Set-Cookie` is not
one, and neither are the date fields (`Date`, `Expires`,
`Last-Modified`, …), whose values contain literal commas — `get_all()`
is for those:

```python
from action0.req import Headers

headers = Headers("Vary: Accept-Encoding, Accept-Language\nVary: Cookie")
print(headers.get_values("vary"))
# ['Accept-Encoding', 'Accept-Language', 'Cookie']
headers = Headers('Cache-Control: private="a, b", max-age=60')
print(headers.get_values("Cache-Control"))
# ['private="a, b"', 'max-age=60']
```

A name or value that would break out of its line on the wire — one
containing a carriage return, a line feed or a NUL — is refused with a
`ValueError`, whichever way it comes in, so a value taken from user
input cannot smuggle in a header of its own. So is a name that a
recipient would not read as that name: an empty one, one with a colon,
and one with whitespace — `"Transfer-Encoding "` would be written as
`Transfer-Encoding : chunked`, the classic request smuggling line. A raw
header block is split at `\r\n` and `\n` only, and a line with
whitespace in its name (also right before the colon) is not a header
line.

```python
from action0.req import Headers

headers = Headers()
for name, value in [("X-Trace", "abc\r\nSet-Cookie: admin=1"), ("Transfer-Encoding ", "chunked")]:
    try:
        headers.add(name, value)
    except ValueError as error:
        print(str(error).split(",")[0])
# value of the header 'X-Trace' contains a carriage return
# header name contains whitespace
print(len(headers))
# 0
```

A value is a `str`, an `int`, a `float` or a `bool`, on its own or in a
list. Anything else raises a `TypeError` — bytes (decode them first),
and `None`, which would otherwise be sent as the text `None`:

```python
from action0.req import Headers

try:
    Headers({"X-Trace": [None]})
except TypeError as error:
    print(error)
    # a header value must be a str, int, float or bool, got NoneType
```

Equality ignores the order of different fields and the casing of names,
but respects the order of a field's own lines. `repr()` and `str()`
redact the values of secret fields (`Authorization`, cookies, … — the
`Headers.secret_names` set, which can be overridden on a subclass or an
instance; copies, and the requests and responses the headers are handed
to, keep the override); only the wire rendering `as_str()` keeps them:

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
method, URL, headers, body and HTTP version — every part a mutable
attribute (the URL a {py:class}`~action0.url.base.Url` from
[action0-url](https://laughinjar.github.io/action0-url/), the headers a
{py:class}`~action0.req.headers.Headers`). The URL can be passed as a
string or an existing `Url` (which is copied); `query` replaces the URL's
query like the `Url` constructor does (`None`, the default, leaves it
alone; an empty mapping removes it); the method is uppercased, also when
it is assigned later:

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

The URL and the headers stay what they are when they are assigned: a
string becomes a `Url`, a mapping (or any other form the `Headers`
constructor takes) becomes `Headers`, so the lookups keep ignoring the
casing. A `Url` or `Headers` instance is taken as it is — only the
constructor copies:

```python
from action0.req import Request

req = Request("https://api.example.com/items")
req.url = "https://api.example.com/users?page=2"
req.headers = {"accept": "application/json"}
print(req.url.path, req.headers["Accept"])
# /users application/json
```

`as_str()` renders the wire format: the request line with the origin-form
target and the headers, deriving `Host` from the URL when none is set
(like `Headers.as_str()` it does NOT redact secret values — `repr()`
does). The method, the target and the HTTP version are the three parts
of the request line, separated by single spaces: one that is empty or
contains whitespace would not be read as that part, and raises a
`ValueError`.

```python
from action0.req import Request

try:
    Request("https://api.example.com/items", target="/admin HTTP/1.0").as_str()
except ValueError as error:
    print(str(error).split(",")[0])
    # request target contains whitespace
```

The URL is kept the way it is written — its percent escapes and the
spelling of its query are neither decoded nor re-encoded — and the
target is that spelling, character for character, so a signed URL
arrives on the wire unchanged. The derived `Host` is what the URL string
has as well: a non-ASCII hostname IDNA-encoded, an IPv6 address in
brackets:

```python
from action0.req import Request

req = Request("https://bücher.example/a%2Fb?sig=x%2By&flag")
print(req.url)
# https://xn--bcher-kva.example/a%2Fb?sig=x%2By&flag
print(req.as_str(separator="\n"))
# GET /a%2Fb?sig=x%2By&flag HTTP/1.1
# Host: xn--bcher-kva.example
```

Nothing is assumed about parts a URL string does not have. Without a
scheme it is a relative reference: `"example.com/items"` is a path, not a
host — write `"https://example.com/items"`. A request without a host has
no `Host` line, and its target is always an absolute path:

```python
from action0.req import Request

req = Request("example.com/items")
print(req.url.hostname, req.url.path)
# None example.com/items
print(req.as_str())
# GET /example.com/items HTTP/1.1
```

The target `as_str()` derives is the origin-form, path and query. The
other forms of a request target cannot be read off a URL, so they are
set explicitly with `target`: `"*"` for a server-wide `OPTIONS`,
`"host:port"` for `CONNECT`, the absolute URL for a request sent to a
proxy:

```python
from action0.req import Request

req = Request("https://example.com", "OPTIONS", target="*")
print(req.as_str(separator="\n"))
# OPTIONS * HTTP/1.1
# Host: example.com
req = Request("//example.com:443", "CONNECT", target="example.com:443")
print(req.as_str(separator="\n"))
# CONNECT example.com:443 HTTP/1.1
# Host: example.com:443
```

`copy()` returns an independent request (of the same class), with
keyword overrides for any constructor argument. `repr()` redacts the
password of the URL and the values of the query parameters that carry
secrets (`api_key`, `token`, `signature`, … — the overridable
`Request.secret_params` set); the URL itself and `as_str()` keep them:

```python
from action0.req import Request

req = Request("https://user:secret@api.example.com/items?api_key=s3cr3t")
clone = req.copy(method="POST", query={"id": 7, "token": "abc"})
print(clone)
# Request(POST https://user:***@api.example.com/items?id=7&token=***)
print(clone.url)
# https://user:secret@api.example.com/items?id=7&token=abc
```

A fragment written like a query — the tokens the OAuth implicit flow
puts behind the `#` — is redacted the same way. A `secret_params` set
on one request is that request's own, and its copies keep it:

```python
from action0.req import Request

print(Request("https://app.example.com/callback#access_token=s3cr3t&state=xyz"))
# Request(GET https://app.example.com/callback#access_token=***&state=xyz)
req = Request("https://api.example.com/items?session=abc")
req.secret_params = req.secret_params | {"session"}
print(req.copy(method="POST"))
# Request(POST https://api.example.com/items?session=***)
```

`Request.from_str()` is the inverse of `as_str()`: it parses the request
line, the header lines and — behind a blank line — the body, which is
taken as text. The URL is put together from the target and the `Host`
header; a request does not say which scheme it was sent with, so pass
`scheme` if you know it. A target that is not a path (`*`, `host:port`,
an absolute URL) is kept as `target`, and rendering the parsed request
gives the text back as it was:

```python
from action0.req import Request

text = "POST /items?page=2 HTTP/1.1\nHost: api.example.com\nAccept: */*\n\nhello"
req = Request.from_str(text, scheme="https")
print(req)
# Request(POST https://api.example.com/items?page=2)
print(req.headers["Accept"], req.body)
# */* hello
print(req.as_str(include_body=True, separator="\n") == text)
# True
```

Two things come back as a request always has them here, whatever the
text said: the method in uppercase, and — for a request without a
`Host` header whose target names the host (an absolute URL, the
`host:port` of a `CONNECT`) — the `Host` derived from it.

The `Host` header has to be a host, optionally with a port. A value with
more to it would turn into the path or the userinfo of the URL
(`trusted.com@evil.com` names the host `evil.com`), and of two `Host`
lines nobody can say which one counts — both raise a `ValueError`, as a
server answers them with a 400:

```python
from action0.req import Request

try:
    Request.from_str("GET /items HTTP/1.1\nHost: trusted.com@evil.com")
except ValueError as error:
    print(str(error).split(",")[0])
    # invalid Host header
```

Both requests and responses carry a `meta` dict for application metadata
— correlation ids, tracing context, per-request knobs for the HTTP layer
on top — never sent on the wire and excluded from equality. `copy()`
gives the clone its own (shallow) copy:

```python
from action0.req import Request

req = Request("https://api.example.com/items", meta={"my-lib.correlation-id": "abc123"})
print(req.copy().meta)
# {'my-lib.correlation-id': 'abc123'}
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

`as_str(include_body=True)` is a rendering for people and never fails
over what the body or its charset say: bytes that are not text in the
charset (a compressed body, an image) show with U+FFFD replacement
characters, and a charset Python cannot decode with is read as utf-8.
The accessors are strict — `body_str()` raises a `UnicodeDecodeError`
for such a body, and a `LookupError` for a charset Python does not know.

A `bytearray` or a `memoryview` is not one of the three forms: the
accessors (and `as_str(include_body=True)`) raise a `TypeError` that
says so — pass `bytes(...)`.

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
chunks — e.g. a generator; the length is unknown, such a body would be
sent chunked. A body over a generator (or any other iterator) can be
read **once**: the chunks are gone when they have been delivered.
Reading it again raises a `RuntimeError` that says so — it does not come
back empty, which would pass for an empty body. Pass a list of chunks
for a body that can be read any number of times:

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
try:
    req.body_bytes()
except RuntimeError as error:
    print(str(error).split(":")[0])
    # this body has been read already

again = IterableBody([b"chunk1", b"chunk2"])  # a list: re-readable
print(again.as_bytes() == again.as_bytes())
# True
```

{py:class}`~action0.req.body.AsyncIterableBody` wraps an asynchronous
iterable — e.g. an async generator proxying another stream. It is
async-only: the synchronous `chunks()`/`as_bytes()` raise a
`RuntimeError`, and like a generator an async generator is read once.
Every producer supports `achunks()` for asynchronous consumption
(`FileBody` runs its file operations in a worker thread — asyncio's
default thread pool, or trio's below trio — so the event loop is never
blocked; a source that may block for good, like a named pipe nobody
writes to, does not stand in the way of a timeout):

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

A request or response reads such a body with `abody_bytes()` and
`abody_str()`, the asynchronous twins of `body_bytes()` and `body_str()`
— they read any producer through its `achunks()`:

```python
import asyncio

from action0.req import AsyncIterableBody, Request


async def generate():
    yield b"async"
    yield b"chunks"


async def main():
    req = Request("https://api.example.com/upload", "PUT", body=AsyncIterableBody(generate()))
    return await req.abody_bytes()


print(asyncio.run(main()))
# b'asyncchunks'
```

`IterableBody.achunks()` reads its (synchronous) iterable where it is
called: on the event loop. That is right for chunks that are there
already, but an iterable that blocks while it produces a chunk — reading
a socket or a file — stalls the loop for as long. Pass `threaded=True`
for such a source; then every chunk is fetched in a worker thread (and
the `close` callback runs there too). The iterable sees the context
variables of the task that reads it there as well:

```python
import asyncio
import time

from action0.req import IterableBody


def slow():
    for chunk in (b"slow", b"chunks"):
        time.sleep(0.01)  # blocking I/O
        yield chunk


async def main():
    return [chunk async for chunk in IterableBody(slow(), threaded=True).achunks()]


print(asyncio.run(main()))
# [b'slow', b'chunks']
```

### Releasing a streamed body

A body streaming from a resource — an open connection, say — takes a
callback that releases it: `close=` for `IterableBody`, an awaitable
`aclose=` for `AsyncIterableBody`. The callback runs exactly once: when
the body has been read to its end, when a consumer gives up midway, or
when the body is closed explicitly. The last case also covers a body
that is never read at all, where a generator's own `finally` block
would not run:

```python
from action0.req import IterableBody


def generate():
    yield b"chunk1"
    yield b"chunk2"


body = IterableBody(generate(), close=lambda: print("connection released"))
with body:
    pass  # decided not to read it
# connection released
body.close()  # no effect: the callback has run
```

Closing the body also closes a generator behind it, ahead of the
callback: a generator that was read only in part gets to run its own
`finally` blocks right away instead of at garbage collection. A body
over a generator cannot be read after it has been closed; one over a
list is not affected. A generator that is running at that moment —
another task is waiting in it for the next chunk — is left alone: the
callback releases what it reads from, which is how a stalled read is
aborted from outside. Without a callback there is nothing that could
stop the read, and closing raises a `RuntimeError` instead of pretending
— cancel the reading task.

A reader of a body that is closed under it does not see a quiet end
either — whether it is between two chunks, or waiting in the generator
when the callback takes its connection away and the generator returns
as if it were done: its read raises a `RuntimeError`, because what it
has got may not be the whole body. (A generator that closes its own body
on the way out is the regular end, not that.)

A source that can be iterated more than once and makes a new generator
for every read — an object whose `__iter__` or `__aiter__` is a
generator function — has the generator of a read closed with that read,
also when the consumer gives up midway.

A request can hold such a body as well — one that was built but never
sent, say. {py:meth}`Request.close() <action0.req.request.Request.close>`
/ {py:meth}`~action0.req.request.Request.aclose` and the request as a
(sync or async) context manager release it, exactly like a response
(see *Closing a response* below).

### Spooled bodies

{py:class}`~action0.req.body.SpooledBody` is the producer for a body
that is stored first and read afterwards: it is written to while the
data arrives, so the connection is free again before anyone looks at
the data. Up to `max_memory` bytes (1 MiB by default) are kept in
memory; the write exceeding that moves everything to an anonymous
temporary file, so memory use stays bounded however large the body is.
It can be read any number of times, and
{py:meth}`~action0.req.body.SpooledBody.file` offers it as a seekable
binary file object:

```python
from action0.req import SpooledBody

with SpooledBody(max_memory=8) as body:
    body.write(b"chunk1")
    print(body.rolled)
    # False
    body.write(b"chunk2")  # exceeds max_memory: moved to a temporary file
    print(body.rolled, body.content_length())
    # True 12
    print(list(body.chunks()) == list(body.chunks()))  # re-readable
    # True
    file = body.file()  # a seekable view, a new one per call
    file.seek(5)
    print(file.read())
    # b'1chunk2'
print(body.closed)  # leaving the block removed the temporary file
# True
```

The temporary file is removed by `close()` or by leaving the `with`
block — garbage collection is only the last resort.

The file object from `file()` is a read-only view with a position of its
own, and so is every iteration of the body. Several consumers can
therefore read the same body at once — two tasks serving one cached
response, say — and closing a view, or handing it to a wrapper that
closes what it wraps (`io.TextIOWrapper`), leaves the body alone. A view
has no file descriptor; `save_body()` puts the data into a real file.

Read a spooled body once it is complete: a reader ends where the data
ends at that moment, and cannot tell a body that is still being written
from a finished one.

If the data cannot be moved to the temporary file — the disk is full —
the write raises and the body is closed: there is no telling how much
made it, and a closed body raises on every read instead of handing out
a part. A later write that fails leaves the body as it was before:
whatever part of it reached the file is not handed out, the body ends
where its last complete write ended.

In asynchronous code use `awrite()`, `achunks()` and `aclose()` (or
`async with`). While the data is in memory they run inline; once it is
in the temporary file every file operation runs in a worker thread
(asyncio's default thread pool, or trio's when running below trio), so
the event loop is never blocked. That is one thread hop per `awrite()`
call — write chunks of tens of kilobytes rather than single bytes.
`aclose()` removes the temporary file also when the task has been
cancelled, e.g. by a timeout:

```python
import asyncio

from action0.req import SpooledBody


async def main():
    async with SpooledBody(max_memory=0) as body:  # 0: on disk from the first byte
        await body.awrite(b"chunk1")
        await body.awrite(b"chunk2")
        return [chunk async for chunk in body.achunks()]


print(asyncio.run(main()))
# [b'chunk1chunk2']
```

## Responses

{py:class}`~action0.req.response.Response` mirrors `Request` for the
server side: status, headers, body and HTTP version as attributes (a
mapping assigned as the headers becomes `Headers`, as for a request),
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
simply have no phrase (the category properties still work for them).
The status line keeps the space behind the status code then, as its
grammar requires:

```python
from action0.req import Response

print(Response(404, reason="Nope"))
# Response(404 Nope)
print(repr(Response(599).as_str()))
# 'HTTP/1.1 599 '
print(Response(599).is_server_error)
# True
```

The status has to be an int; anything else — the string `"404"`, say,
or a `bool` — raises a `TypeError` when it is set, not later in a
comparison. The HTTP version must be one word: `as_str()` raises a
`ValueError` for one that is empty or contains whitespace.

`Response.from_str()` parses the text of a response, as
`Request.from_str()` does for a request. The phrase of the status line
becomes the `reason`, and the body behind the blank line is taken as
text:

```python
from action0.req import Response

text = "HTTP/1.1 404 Not Found\nContent-Type: text/plain\n\nnot here"
resp = Response.from_str(text)
print(resp.status, resp.reason, resp.headers["Content-Type"], resp.body)
# 404 Not Found text/plain not here
print(resp.as_str(include_body=True, separator="\n") == text)
# True
```

A status line without a phrase is taken at its word: the reason is the
empty string, and no phrase from the registry is put in its place. (The
space behind the status code is always written, also where the text had
none, and the status code as the number it is.)

```python
from action0.req import Response

resp = Response.from_str("HTTP/1.1 200 \nETag: abc")
print(repr(resp.reason), repr(resp.as_str(separator="\n")))
# '' 'HTTP/1.1 200 \nETag: abc'
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

### Response bodies as files

{py:meth}`~action0.req.response.Response.body_file` returns the body as
a seekable binary file object — for consumers that read from a file,
like streaming parsers or decompressors. For an in-memory body that is
an in-memory file; a {py:class}`~action0.req.body.SpooledBody` hands out
a read-only view of itself; a {py:class}`~action0.req.body.FileBody` hands out the
file it wraps, or opens its path anew for every call — nothing is
copied, and what you have not closed yourself is closed with the
response; and a streamed body is read to its end into a new
`SpooledBody`, which then replaces `resp.body`, so the response stays
readable — any number of times, where the stream could be read only
once. The stream it replaces has delivered everything and is closed,
which releases its connection. `max_memory` and `dir` are passed on to
the `SpooledBody`:

```python
import gzip
import json

from action0.req import IterableBody, Response

compressed = gzip.compress(json.dumps({"rows": [1, 2, 3]}).encode())
resp = Response(200, body=IterableBody([compressed[:10], compressed[10:]]))  # a streamed body

with resp:  # closing the response discards the spooled data
    with gzip.GzipFile(fileobj=resp.body_file()) as report:
        print(json.load(report))
        # {'rows': [1, 2, 3]}
    print(type(resp.body).__name__)
    # SpooledBody
```

The replacement happens in this response only. A `copy()` made before
shares the original stream, which is used up afterwards — as it is after
any other read of a streamed body.

`body_file()` reads the stream synchronously. In asynchronous code
{py:meth}`~action0.req.response.Response.abody_file` does the same
without blocking the event loop: it reads through `achunks()`, spools
with `awrite()` and awaits the stream's `aclose()`. It is also how a
body that can only be read asynchronously becomes a file:

```python
import asyncio

from action0.req import AsyncIterableBody, Response


async def stream():
    yield b"chunk1"
    yield b"chunk2"


async def main():
    async with Response(200, body=AsyncIterableBody(stream())) as resp:
        file = await resp.abody_file()
        print(file.read())
        # b'chunk1chunk2'
        print(type(resp.body).__name__, await resp.abody_bytes())  # readable again
        # SpooledBody b'chunk1chunk2'


asyncio.run(main())
```

### Saving a response body

{py:meth}`~action0.req.response.Response.save_body` copies the body into
a path or an open binary file object, chunk by chunk — a streamed body
never has to fit in memory. A path is created or truncated, or appended
to with `append=True`; a file object is written at its current position
and left open. The return value is the number of bytes written.

Because the response is in your hands before anything is written, you
decide whether its body belongs in the file. Resuming a download with an
HTTP `Range` request, for example, appends only if the server really
answered with the requested part — an error page never ends up in the
file:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from action0.req import IterableBody, Response

with TemporaryDirectory() as tmp:
    path = Path(tmp) / "feed.json"
    path.write_bytes(b'{"items": [1, 2')  # an earlier download that broke off
    offset = path.stat().st_size

    # the answer to a request sent with "Range: bytes=15-"
    resp = Response(
        206,
        headers={"Content-Range": f"bytes {offset}-19/20"},
        body=IterableBody([b", 3", b"]}"]),
    )
    with resp:
        if resp.status == 206 and resp.headers["Content-Range"].startswith(f"bytes {offset}-"):
            print(resp.save_body(path, append=True))
            # 5
    print(path.read_bytes())
    # b'{"items": [1, 2, 3]}'
```

If the body breaks off midway, the exception passes through and what
has arrived is in the file — its size is the offset to resume from. A
path is opened only once the body has delivered its first chunk: a body
that cannot be read at all (already closed, a stream that has been read
before, failing right away) raises without creating or truncating the
file. If it is the write that fails
(a full disk), the stream is closed, which releases its connection.

{py:meth}`~action0.req.response.Response.asave_body` is the asynchronous
twin: it reads the body via `achunks()` and does every file operation in
a worker thread, writing small chunks 64 KiB at a time. A cancellation
(a timeout) does not tear anything apart: a write that is under way is
finished, and once the file is open, what has arrived is written and the
file closed before the cancellation is passed on. Both exist as
functions too ({py:func}`~action0.req.body.save_body`,
{py:func}`~action0.req.body.asave_body`), for a body that is not part of
a response.

### Closing a response

A response whose body holds a resource should be closed: a streamed
body that was not read to its end still occupies its connection, a
spooled body its temporary file. Use the response as a context manager
(`with` or `async with`), or call
{py:meth}`~action0.req.response.Response.close` /
{py:meth}`~action0.req.response.Response.aclose`. For a body that holds
nothing (bytes, str, none) both do nothing, so closing every response
is always safe:

```python
from action0.req import IterableBody, Response

body = IterableBody(iter([b"never", b"read"]), close=lambda: print("connection released"))
with Response(200, body=body) as resp:
    print(resp.status)
    # 200
# connection released

with Response(200, body=b"plain") as resp:  # nothing to release
    print(resp.body_bytes())
    # b'plain'
```

`copy()` shares the body, so closing one copy closes it for all of
them. A body that can only be closed asynchronously (an
`AsyncIterableBody` with an `aclose` callback, or over an async
generator that was read in part) makes `close()` raise a `RuntimeError`
instead of silently leaving the resource open.

When a `with` or `async with` block is left by an exception, that
exception is the one you see: a failure to close does not replace it,
but is attached to it as a note.

### Pickling

A response with an in-memory body (bytes or str) can be pickled, with
its headers, `meta` and request reference — which is what storing
responses outside the process (a cache server, a queue) needs:

```python
import pickle

from action0.req import Response

resp = Response(200, headers={"Content-Type": "application/json"}, body=b'{"a": 1}')
restored = pickle.loads(pickle.dumps(resp))
print(restored == resp, restored.body_bytes())
# True b'{"a": 1}'
```

The request reference is pickled along. Drop it first
(`resp.copy(request=None)`) if the request carries credentials that
should not end up in the store.

A body that holds a resource cannot be pickled: a stream is read once,
a `SpooledBody` has a temporary file. Read it into memory first
(`resp.copy(body=resp.body_bytes())`).

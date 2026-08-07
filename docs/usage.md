# Guide

Everything here is runnable as-is; the `#` comments show the exact output.

The library is under construction — this guide grows together with the
API (`Headers`, `Request`, `Response`, the header and status constants,
and body streaming).

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

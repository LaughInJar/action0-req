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

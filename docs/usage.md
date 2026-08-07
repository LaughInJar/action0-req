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

## Version

```python
from action0.req import __version__

print(type(__version__))
# <class 'str'>
```

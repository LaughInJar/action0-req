# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`action0-req` is a Python library for representing, creating and manipulating HTTP requests and responses. It ships the `action0.req` package (`action0` is a PEP 420 namespace package) from a `src/` layout, is built with hatchling, and uses `uv` for environment/dependency management. Its only runtime dependency is `action0-url` (the URL representation), which is not on PyPI yet and is resolved from GitHub via `[tool.uv.sources]`.

## Rules

- **Never commit without asking.** Also never push, tag, or publish on your own.
- **Branches + PRs.** All changes go through feature branches and GitHub pull requests that Simon reviews and merges — never commit to `main` directly. (Only the initial implementation was built directly on `main`; that phase is over.)
- **Discuss first.** Always present the plan and the intended edits and get agreement before changing files.
- Every code change comes with: tests, docstrings, inline comments where the code isn't self-explanatory, and updated usage examples in `README.md` and the Sphinx docs (`docs/usage.md`).
- Before considering work done, run ruff, mypy, and pytest (commands below) and fix what they report.
- Supported Python versions: 3.11 up to the latest release. Don't use syntax or stdlib features introduced after 3.11, and don't rely on behavior removed in newer versions.

## Commands

`uv run` syncs the environment automatically (the dev dependency group is installed by default), so no separate install step is needed.

```sh
uv run pytest                                    # all tests
uv run pytest tests/action0/req/test_init.py     # one file
uv run pytest tests/action0/req/test_init.py::PackageTestCase::test_version  # one test

uv run ruff check      # lint (add --fix to autofix)
uv run ruff format     # format
uv run mypy            # type-check (strict; files are configured in pyproject.toml)
uv run pyright         # type-check
uv run ty check        # type-check

uv run --group docs sphinx-build -W --keep-going -b html docs docs/_build/html  # build docs

uv build               # build sdist + wheel into dist/
```

`pytest` also runs the `>>>` examples in the docstrings as doctests (`--doctest-modules` over `src/`), so docstring examples must produce their shown output exactly.

## Architecture

The package is being built up module by module under `src/action0/req/`. Planned/current layout:

- `headers.py` — `Headers`: ordered, case-insensitive, multi-value aware mapping of header fields, internally a `list[tuple[str, str]]` of lines preserving the representation (order and casing) exactly. The `MutableMapping` view is single-value (last line per field, like `Params`); `get_all()`/`get_values()` (RFC 9110 comma-splitting)/`add()`/`set()`/`remove()`/`update()`/`as_lines()`/`as_dict()`/`as_str()` handle multiple lines. No underscore→dash normalization (header smuggling footgun). `__eq__` ignores field order and name casing but respects per-field line order; `repr()`/`str()` redact the values of `Headers.secret_names` with `***` — only the wire rendering `as_str()` keeps them. Also `Header`: `StrEnum` of the IANA HTTP field name registry (permanent + deprecated + provisional registrations, generated from the registry CSV on 2026-08-07) plus a block of unregistered de-facto `X-…` names.
- `status.py` — `Status`: custom `IntEnum` of the IANA status code registry with `phrase` and `is_informational`/`is_success`/`is_redirection`/`is_client_error`/`is_server_error` properties. Deliberately not `http.HTTPStatus`: the stdlib's code list and category properties vary across 3.11–3.15. Two pre-RFC-9110 aliases (`PAYLOAD_TOO_LARGE`, `UNPROCESSABLE_ENTITY`).
- `request.py` — `Method`: `StrEnum` of the RFC 9110 verbs + `PATCH` + `QUERY`. `Request`: method (uppercased str), `url` (`action0.url.Url` — copied on ingestion, `query=` kwarg replaces the URL's query like the `Url` constructor), `headers` (`Headers`), `body` (`bytes | str | BodyProducer | None`, stored as set), `http_version` and a `meta` dict (application metadata: never on the wire, excluded from `__eq__`, shallow-copied by `copy()`; the constructor copies the given mapping) as plain attributes. Typed body accessors `body_bytes()`/`body_str()`/`body_producer()` convert between the three forms using the Content-Type charset (default utf-8). `copy(**overrides)` (own `Url`/`Headers`; a `BodyProducer` body is shared), part-wise `__eq__` (body compared as set), `repr()` redacts via `Url.__repr__` and omits headers/body, `as_str(include_body=False, separator="\r\n")` renders the wire format with origin-form target and derived `Host` (secrets included; a `BodyProducer` is never consumed — placeholder).
- `body.py` — `BodyProducer`: runtime-checkable `Protocol` for streaming bodies: `content_length()` (`None` = unknown), sync `chunks()`, async `achunks()`, `as_bytes()`. Implementations: `BytesBody` (in-memory, single chunk), `FileBody` (path or open binary file object + `chunk_size`; paths open freshly per iteration → re-iterable, seekable file objects are rewound per iteration and never closed, non-seekable ones are single-use with unknown length; `achunks()` does all file ops via `asyncio.to_thread`), `IterableBody` (wraps `Iterable[bytes]`, unknown length, generator sources single-use), `AsyncIterableBody` (wraps `AsyncIterable[bytes]`, async-only: sync `chunks()`/`as_bytes()` raise `RuntimeError`). Also the `BodyTypes` alias and the private conversion helpers (`_charset`, `_body_bytes`/`_body_str`/`_body_producer`, `_rendered_body`) shared by `Request` and `Response` — the classes keep their own thin, documented accessor methods.
- `response.py` — `Response`: `status` (any int; `Status` members welcome), `headers`, `body`, `reason` (server-sent phrase; the `phrase` property falls back to the `Status` registry, `""` for unknown codes), `http_version`, an optional `request` back-reference (metadata: shared, not copied, excluded from `__eq__`) and a `meta` dict like `Request`'s. Category properties (`is_success`, …) computed from the numeric range so unregistered codes work. Same body accessors as `Request`; `copy(**overrides)`, `as_str(include_body, separator)` with status line, redacting-by-omission `repr()` (`Response(404 Not Found)`).

Conventions:

- The version is single-sourced as `__version__` in `src/action0/req/__init__.py`; hatch extracts it with the regex in `[tool.hatch.version]`. Bump it only there.
- Releases: pushing a `vX.Y.Z` tag triggers `.github/workflows/release.yml`, which re-runs all checks, verifies the tag matches `__version__`, builds, and publishes to PyPI via trusted publishing (environment `pypi`). Never bump the version, tag, or publish on your own — releasing is the user's call.
- Tests mirror the `src/` layout under `tests/action0/req/` and are `unittest.TestCase` classes, executed via pytest.
- Ruff enforces one import per line (isort `force-single-line`), line length 99, `action0` as first-party.
- Docs live in `docs/` (Sphinx + Furo, MyST Markdown pages, autodoc for the API reference). Docstrings are Sphinx-reST (`:param:`, `:py:meth:` roles). CI builds them with `-W` on every run and deploys to GitHub Pages on pushes to `main`. Guide examples in `docs/usage.md` show exact outputs in `#` comments — keep them truthful.

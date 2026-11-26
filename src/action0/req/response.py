"""The HTTP response representation (:py:class:`Response`)."""

import inspect
import io
import os
from types import TracebackType
from typing import IO
from typing import Any
from typing import Mapping
from typing import Self
from typing import Union
from typing import cast

from .body import BodyProducer
from .body import BodyTypes
from .body import BytesBody
from .body import FileBody
from .body import SpooledBody
from .body import _abody_bytes
from .body import _abody_str
from .body import _aclose_body
from .body import _aclose_on_exit
from .body import _body_bytes
from .body import _body_producer
from .body import _body_str
from .body import _charset
from .body import _close_body
from .body import _close_on_exit
from .body import _in_thread
from .body import _rendered_body
from .body import _SpooledStream
from .body import asave_body
from .body import save_body
from .headers import Headers
from .headers import HeaderTypes
from .headers import _check_safe
from .headers import _own_headers
from .request import Request
from .request import _split_message
from .status import Status


class Response:
    r"""
    Python representation of an HTTP response: status, headers, body and
    HTTP version — every part a plain mutable attribute (the headers a
    :py:class:`~action0.req.headers.Headers`).

    Example::

        >>> resp = Response(404, headers={"Content-Type": "text/plain"}, body="not here")
        >>> resp.status
        404
        >>> resp.phrase
        'Not Found'
        >>> resp.is_client_error
        True
        >>> print(resp.as_str(include_body=True, separator="\n"))
        HTTP/1.1 404 Not Found
        Content-Type: text/plain
        <BLANKLINE>
        not here
        >>> resp
        Response(404 Not Found)

    The body works exactly like the request body: set as ``bytes``,
    ``str`` or a streaming :py:class:`~action0.req.body.BodyProducer`,
    retrieved in any of the three forms via :py:meth:`body_bytes`,
    :py:meth:`body_str` and :py:meth:`body_producer`
    (:py:meth:`abody_bytes` and :py:meth:`abody_str` in asynchronous
    code). On top of that a response body can be read as a file
    (:py:meth:`body_file`, :py:meth:`abody_file`) and copied into one
    (:py:meth:`save_body`, :py:meth:`asave_body`).

    A streamed body can be read once: reading it again raises a
    :py:class:`RuntimeError` rather than giving an empty result. Ask for
    :py:meth:`body_file` first if the body is needed more than once — it
    keeps the data.

    A response whose body holds a resource — a streamed body its
    connection, a spooled body its temporary file — is released with
    :py:meth:`close` or :py:meth:`aclose`, or by using the response as a
    (sync or async) context manager. Both are harmless no-ops for a plain
    bytes or str body::

        >>> with Response(200, body=b"data") as resp:
        ...     resp.body_bytes()
        b'data'

    The attributes: :py:attr:`status`, :py:attr:`reason`,
    :py:attr:`headers`, :py:attr:`body`, :py:attr:`http_version`,
    :py:attr:`request` and :py:attr:`meta`.
    """

    headers: Headers
    """The header fields, the response's own
    :py:class:`~action0.req.headers.Headers` instance."""

    body: Union[BodyTypes, None]
    """The body as it was set: ``bytes``, ``str``, a
    :py:class:`~action0.req.body.BodyProducer` or ``None``.
    :py:meth:`body_file` replaces a streamed body with the
    :py:class:`~action0.req.body.SpooledBody` it was read into."""

    reason: Union[str, None]
    """The reason phrase as sent by the server, ``None`` if there is
    none — :py:attr:`phrase` falls back to the registry then."""

    http_version: str
    """The protocol version rendered in the status line."""

    request: Union[Request, None]
    """The request that produced this response, if known — metadata:
    shared with :py:meth:`copy`, not part of :py:meth:`__eq__`."""

    meta: dict[str, Any]
    """Application metadata riding along with the response — e.g. a
    backend's native response object, cache markers, timings — never
    sent on the wire and not part of :py:meth:`__eq__`. The dict is
    the response's own (the constructor copies the given mapping);
    libraries should namespace their keys."""

    def __init__(
        self,
        status: int = Status.OK,
        *,
        headers: Union[HeaderTypes, None] = None,
        body: Union[BodyTypes, None] = None,
        reason: Union[str, None] = None,
        http_version: str = "HTTP/1.1",
        request: Union[Request, None] = None,
        meta: Union[Mapping[str, Any], None] = None,
    ) -> None:
        """
        :param status: the status code, a :py:class:`~action0.req.status.Status`
                       member or any int — unregistered codes are fine
        :param headers: the initial header lines, in any form accepted by
                        :py:class:`~action0.req.headers.Headers`; a
                        ``Headers`` instance is copied (keeping its class
                        and its ``secret_names``)
        :param body: the response body as ``bytes``, ``str`` or a streaming
                     :py:class:`~action0.req.body.BodyProducer`
        :param reason: the reason phrase as sent by the server; ``None``
                       falls back to the registry phrase of the status code
                       (see :py:attr:`phrase`)
        :param http_version: the protocol version rendered in the status
                             line
        :param request: the request that produced this response — metadata:
                        shared, not copied, and not part of equality
        :param meta: initial application metadata riding along with the
                     response (see :py:attr:`meta`)
        :raises TypeError: if the status is not an int
        """
        self.status = status
        self.headers = _own_headers(headers)
        self.body = body
        self.reason = reason
        self.http_version = http_version
        self.request = request
        self.meta = dict(meta) if meta is not None else {}

    @property
    def status(self) -> int:
        """
        The status code: any int, registered or not — a
        :py:class:`~action0.req.status.Status` member is one. Setting
        anything else (the string ``"404"``, say) raises a
        :py:class:`TypeError` right away instead of failing later in a
        comparison.
        """
        # kept in the instance dict under its public name: that is where
        # a plain attribute lives, so pickles from before this was a
        # property still load
        return cast("int", self.__dict__["status"])

    @status.setter
    def status(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError(
                f"the status must be an int (a Status member is one), "
                f"got {type(value).__name__}: {value!r}"
            )
        self.__dict__["status"] = value

    @classmethod
    def from_str(cls, text: str) -> Self:
        r"""
        Parse the text of a response — the inverse of :py:meth:`as_str`:
        the status line, the header lines and, behind a blank line, the
        body. Lines may end in ``"\r\n"`` or ``"\n"``.

        Example::

            >>> resp = Response.from_str("HTTP/1.1 404 Not Found\nContent-Type: text/plain\n\ngone")
            >>> resp
            Response(404 Not Found)
            >>> resp.headers
            Headers(Content-Type: text/plain)
            >>> resp.body
            'gone'

        The phrase of the status line becomes the :py:attr:`reason` —
        it is what the server sent, also where it equals the registry
        phrase. ``Response.from_str(text).as_str(...)`` gives the text
        back as it was (with ``include_body=True`` and the separator of
        the text).

        :param text: the response as text; the body is whatever follows
                     the first blank line and is taken as a ``str``
                     (``None`` if there is no blank line or nothing
                     behind it)
        :return: the response
        :raises ValueError: if the status line does not consist of a
                            version, a numeric status code and
                            (optionally) a phrase, or a header line is
                            invalid
        """
        start, fields, body = _split_message(text)
        # the phrase may contain spaces, and may be missing
        parts = start.split(" ", 2)
        if len(parts) < 2 or not parts[0] or not (parts[1].isascii() and parts[1].isdigit()):
            raise ValueError(f"invalid status line: {start!r}")
        reason = parts[2] if len(parts) > 2 and parts[2] else None
        return cls(
            int(parts[1]),
            headers=Headers(fields),
            body=body,
            reason=reason,
            http_version=parts[0],
        )

    @property
    def phrase(self) -> str:
        """
        The reason phrase: the explicitly set :py:attr:`reason` if there is
        one, otherwise the registry phrase of the status code, otherwise
        (for unregistered codes) an empty string.
        """
        if self.reason is not None:
            return self.reason
        try:
            return Status(self.status).phrase
        except ValueError:
            return ""

    @property
    def is_informational(self) -> bool:
        """``True`` for 1xx statuses (works for any int status)."""
        return 100 <= self.status <= 199

    @property
    def is_success(self) -> bool:
        """``True`` for 2xx statuses (works for any int status)."""
        return 200 <= self.status <= 299

    @property
    def is_redirection(self) -> bool:
        """``True`` for 3xx statuses (works for any int status)."""
        return 300 <= self.status <= 399

    @property
    def is_client_error(self) -> bool:
        """``True`` for 4xx statuses (works for any int status)."""
        return 400 <= self.status <= 499

    @property
    def is_server_error(self) -> bool:
        """``True`` for 5xx statuses (works for any int status)."""
        return 500 <= self.status <= 599

    def body_bytes(self) -> Union[bytes, None]:
        """
        The body as bytes, regardless of how it was set: bytes are
        returned as-is, a str is encoded with the Content-Type charset
        (default utf-8), a :py:class:`~action0.req.body.BodyProducer` is
        read in full.

        A streamed body (one over a generator or a connection) can be
        read only once: keep the result, or call :py:meth:`body_file`
        first, which keeps the data. A second read raises a
        :py:class:`RuntimeError`.

        :return: the body bytes, ``None`` if there is no body
        :raises LookupError: if a str body is to be encoded and Python
                             does not know the Content-Type charset
        :raises UnicodeEncodeError: if a str body cannot be encoded with
                                    the charset
        :raises RuntimeError: for a producer that cannot be read
                              (again), or only asynchronously — use
                              :py:meth:`abody_bytes` then
        """
        return _body_bytes(self.body, _charset(self.headers))

    def body_str(self) -> Union[str, None]:
        """
        The body as text, regardless of how it was set: a str is returned
        as-is, bytes are decoded with the Content-Type charset (default
        utf-8), a :py:class:`~action0.req.body.BodyProducer` is read in
        full and decoded.

        :return: the body text, ``None`` if there is no body
        :raises LookupError: if Python does not know the Content-Type
                             charset
        :raises UnicodeDecodeError: if the body is not text in the
                                    charset (binary data, say)
        :raises RuntimeError: for a producer that cannot be read
                              (again), or only asynchronously — use
                              :py:meth:`abody_str` then
        """
        return _body_str(self.body, _charset(self.headers))

    async def abody_bytes(self) -> Union[bytes, None]:
        """
        :py:meth:`body_bytes` for asynchronous code: a
        :py:class:`~action0.req.body.BodyProducer` is read via its
        ``achunks()`` — which is the only way to read an
        :py:class:`~action0.req.body.AsyncIterableBody`, and keeps a
        file-backed body from blocking the event loop.

        :return: the body bytes, ``None`` if there is no body
        :raises LookupError: as for :py:meth:`body_bytes`
        :raises UnicodeEncodeError: as for :py:meth:`body_bytes`
        :raises RuntimeError: for a producer that cannot be read (again)
        """
        return await _abody_bytes(self.body, _charset(self.headers))

    async def abody_str(self) -> Union[str, None]:
        """
        :py:meth:`body_str` for asynchronous code, see
        :py:meth:`abody_bytes`.

        :return: the body text, ``None`` if there is no body
        :raises LookupError: as for :py:meth:`body_str`
        :raises UnicodeDecodeError: as for :py:meth:`body_str`
        :raises RuntimeError: for a producer that cannot be read (again)
        """
        return await _abody_str(self.body, _charset(self.headers))

    def body_producer(self) -> Union[BodyProducer, None]:
        """
        The body as a streaming producer, regardless of how it was set: a
        :py:class:`~action0.req.body.BodyProducer` is returned as-is,
        bytes and str are wrapped in an in-memory
        :py:class:`~action0.req.body.BytesBody` (str encoded with the
        Content-Type charset first).

        :return: the body producer, ``None`` if there is no body
        """
        return _body_producer(self.body, _charset(self.headers))

    def body_file(
        self,
        *,
        max_memory: int = 1048576,
        dir: Union[str, "os.PathLike[str]", None] = None,
    ) -> Union[IO[bytes], None]:
        """
        The body as a seekable binary file object positioned at the
        start — for consumers that read from a file, like streaming
        parsers or ``gzip.GzipFile(fileobj=...)``::

            >>> import gzip
            >>> resp = Response(200, body=gzip.compress(b"report"))
            >>> gzip.GzipFile(fileobj=resp.body_file()).read()
            b'report'

        What the file is depends on the body:

        - bytes, str and :py:class:`~action0.req.body.BytesBody`: a new,
          independent in-memory file per call.
        - :py:class:`~action0.req.body.SpooledBody`: a new read-only
          view of the body per call (see :py:meth:`SpooledBody.file()
          <action0.req.body.SpooledBody.file>`). Closing it does not
          affect the body.
        - :py:class:`~action0.req.body.FileBody` over a seekable file
          object: that file object, rewound. It stays yours — neither
          the body nor :py:meth:`close` closes it.
        - ``FileBody`` over the path of a regular file: a newly opened
          file per call, with a position of its own. Close it when you
          are done; :py:meth:`close` closes those still open. Nothing is
          copied.
        - any other producer (e.g. a streamed body): the stream is read
          to its end into a new ``SpooledBody``, which then **replaces**
          :py:attr:`body` — so the response stays readable, and
          :py:meth:`close` removes the temporary file. The producer it
          replaces has delivered everything and is closed (if it has a
          ``close()``), which releases its connection; one that can only
          be closed asynchronously is closed by :py:meth:`aclose`. This
          reads synchronously; in asynchronous code use
          :py:meth:`abody_file`. If reading the stream fails, the error
          passes through and nothing is kept — also when it is the
          stream's own ``close`` callback that fails, at the very end:
          from here that cannot be told from a stream breaking off.

        NOTE: the replacement happens in this response only. A
        :py:meth:`copy` made before shares the original producer, which
        is used up afterwards — as it is after any other read.

        :param max_memory: for a stream that is spooled: how many bytes
                           the ``SpooledBody`` keeps in memory before it
                           moves to a temporary file (1 MiB by default)
        :param dir: for a stream that is spooled: the directory for the
                    temporary file, by default the system's
        :return: the file object, ``None`` if there is no body
        :raises RuntimeError: for a streamed body that has been read
                              before, or that can only be read
                              asynchronously
        """
        body = self.body
        if body is None:
            return None
        if isinstance(body, SpooledBody):
            return body.file()
        if isinstance(body, (bytes, str, BytesBody)):
            return io.BytesIO(_body_bytes(body, _charset(self.headers)) or b"")
        if isinstance(body, FileBody):
            # None if there is no seekable file with the contents of the
            # body: then it is read like any other stream below
            file = body._file_view()
            if file is not None:
                return file
        close = getattr(body, "close", None)
        spool: SpooledBody
        if callable(close) or getattr(body, "aclose", None) is None:
            spool = SpooledBody(max_memory=max_memory, dir=dir)
        else:
            # a stream that only an awaited aclose() can release: the
            # spool takes that along, see _SpooledStream
            spool = _SpooledStream(max_memory=max_memory, dir=dir)
        try:
            for chunk in body.chunks():
                spool.write(chunk)
        except BaseException:
            # nothing refers to the half-filled spool: don't leave its
            # temporary file to the garbage collector
            spool.close()
            raise
        # replaced first, released afterwards: should the close() below
        # fail, the caller gets that error with the response in order —
        # holding the spool, readable and closable. (A producer closing
        # itself at the end of chunks(), like IterableBody, fails in the
        # loop above instead.)
        self.body = spool
        # the response no longer refers to the producer, so close() could
        # not reach it later: release what it holds now
        if isinstance(spool, _SpooledStream):
            spool.release_with(getattr(body, "aclose"))
        elif callable(close):
            close()
        return spool.file()

    async def abody_file(
        self,
        *,
        max_memory: int = 1048576,
        dir: Union[str, "os.PathLike[str]", None] = None,
    ) -> Union[IO[bytes], None]:
        """
        :py:meth:`body_file` for asynchronous code: a streamed body is
        read via its ``achunks()`` and spooled without blocking the event
        loop (see :py:meth:`SpooledBody.awrite()
        <action0.req.body.SpooledBody.awrite>`), and the producer it
        replaces is closed with its ``aclose()`` if it has one. This is
        how an :py:class:`~action0.req.body.AsyncIterableBody` becomes a
        file — and re-readable.

        Everything else is as in :py:meth:`body_file`, including what
        the file is for each kind of body. The file object itself is an
        ordinary, synchronous one: reading a large body from a temporary
        file blocks while it reads.

        :param max_memory: as for :py:meth:`body_file`
        :param dir: as for :py:meth:`body_file`
        :return: the file object, ``None`` if there is no body
        :raises RuntimeError: for a streamed body that has been read
                              before
        """
        body = self.body
        if body is None:
            return None
        if isinstance(body, SpooledBody):
            return body.file()
        if isinstance(body, (bytes, str, BytesBody)):
            return io.BytesIO(_body_bytes(body, _charset(self.headers)) or b"")
        if isinstance(body, FileBody):
            # looks at the file system and may open a file. The body
            # keeps track of what it opens, so a file opened under a
            # cancellation is not lost: FileBody.close() closes it
            file = await _in_thread(body._file_view)
            if file is not None:
                return file
        spool = SpooledBody(max_memory=max_memory, dir=dir)
        chunks = body.achunks()
        try:
            try:
                async for chunk in chunks:
                    await spool.awrite(chunk)
            finally:
                # matters when the read broke off, as in asave_body()
                stream: object = chunks
                if inspect.isasyncgen(stream):
                    await stream.aclose()
        except BaseException:
            # as in body_file(): nothing refers to the half-filled spool
            await spool.aclose()
            raise
        # replaced first, released afterwards, as in body_file()
        self.body = spool
        await _aclose_body(body)
        return spool.file()

    def save_body(
        self,
        target: Union[str, "os.PathLike[str]", IO[bytes]],
        *,
        append: bool = False,
    ) -> int:
        """
        Copy the body into a file, chunk by chunk — a streamed body is
        never held in memory as a whole. See
        :py:func:`~action0.req.body.save_body` for the details.

        Example — check the response first, write afterwards, so that an
        error body never ends up in the file::

            >>> import io
            >>> download = io.BytesIO(b"first half, ")
            >>> _ = download.seek(0, io.SEEK_END)
            >>> resp = Response(206, headers={"Content-Range": "bytes 12-22/23"}, body=b"second half")
            >>> if resp.status == 206:
            ...     resp.save_body(download)
            11
            >>> download.getvalue()
            b'first half, second half'

        :param target: a path (created or truncated, or appended to with
                       ``append=True``) or a binary file object (written
                       at its current position and left open)
        :param append: for a path: write behind the existing contents
        :return: the number of bytes written; ``0`` if there is no body
        """
        return save_body(self._body_for_saving(), target, append=append)

    async def asave_body(
        self,
        target: Union[str, "os.PathLike[str]", IO[bytes]],
        *,
        append: bool = False,
    ) -> int:
        """
        :py:meth:`save_body` for asynchronous code: the body is read via
        ``achunks()`` and all file operations run in a worker thread. See
        :py:func:`~action0.req.body.asave_body`.

        :param target: a path or a binary file object, as for
                       :py:meth:`save_body`
        :param append: for a path: write behind the existing contents
        :return: the number of bytes written; ``0`` if there is no body
        """
        return await asave_body(self._body_for_saving(), target, append=append)

    def _body_for_saving(self) -> Union[bytes, BodyProducer]:
        """
        :return: the body in a form the save helpers take: a producer
                 as-is (to be streamed), everything else as bytes
        """
        body = self.body
        if body is None or isinstance(body, (bytes, str)):
            return _body_bytes(body, _charset(self.headers)) or b""
        return body

    def close(self) -> None:
        """
        Release what the body holds: the connection of a streamed body
        that was not read to its end, the temporary file of a spooled
        body. Safe to call any number of times, and a no-op for bodies
        that hold nothing (bytes, str, no body).

        A closed body can no longer be read. Note that :py:meth:`copy`
        shares the body: closing one copy closes it for all of them.

        :raises RuntimeError: if the body holds something that can only
                              be released asynchronously (an ``aclose``
                              callback still to run, a producer with
                              ``aclose()`` but no ``close()``) — use
                              :py:meth:`aclose`
        """
        _close_body(self.body)

    async def aclose(self) -> None:
        """
        :py:meth:`close` for asynchronous code: awaits the body's
        ``aclose()`` if it has one, and falls back to its ``close()``.
        """
        await _aclose_body(self.body)

    def __enter__(self) -> Self:
        """
        :return: the response itself; leaving the ``with`` block closes
                 it, see :py:meth:`close`
        """
        return self

    def __exit__(
        self,
        exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        traceback: Union[TracebackType, None],
    ) -> None:
        """
        Close the response, see :py:meth:`close`.

        When the block is left by an exception, that error is the one
        to report: a failure to close — for one, the
        :py:class:`RuntimeError` of a body that can only be closed
        asynchronously — does not replace it, but is attached to it as a
        note.
        """
        _close_on_exit(self.body, exc_value, "response")

    async def __aenter__(self) -> Self:
        """
        :return: the response itself; leaving the ``async with`` block
                 closes it, see :py:meth:`aclose`
        """
        return self

    async def __aexit__(
        self,
        exc_type: Union[type[BaseException], None],
        exc_value: Union[BaseException, None],
        traceback: Union[TracebackType, None],
    ) -> None:
        """
        Close the response, see :py:meth:`aclose`. As in ``with``, a
        failure to close does not replace an exception the block was left
        by, but is attached to it as a note.
        """
        await _aclose_on_exit(self.body, exc_value, "response")

    def copy(self, **overrides: Any) -> Self:
        """
        An independent copy of this response (with its own ``Headers``
        instance and ``meta`` dict), optionally with attributes replaced.
        The body and the ``request`` reference are carried over as-is —
        in particular a :py:class:`~action0.req.body.BodyProducer` is
        shared, not copied; the ``meta`` values are shared too (shallow
        copy). The copy is of the same class as this response, and so are
        its headers.

        Sharing a streamed body means sharing its single use: once one
        copy has read it (also by :py:meth:`body_file`, which keeps the
        data for that copy alone), the others find it used up.

        Example::

            >>> resp = Response(200, body="ok")
            >>> resp.copy(status=404, reason="Nope")
            Response(404 Nope)

        :param overrides: any attribute accepted by the constructor
        :return: a new Response, this instance is not modified
        """
        kwargs: dict[str, Any] = {
            "status": self.status,
            "headers": self.headers,
            "body": self.body,
            "reason": self.reason,
            "http_version": self.http_version,
            "request": self.request,
            "meta": self.meta,
        }
        kwargs.update(overrides)
        return self.__class__(**kwargs)

    def as_str(self, include_body: bool = False, separator: str = "\r\n") -> str:
        r"""
        The wire representation: the status line (with the
        :py:attr:`phrase`), the header lines and optionally the body.
        Secret header values are NOT redacted here, unlike in ``repr()``.

        A status line always has the space behind the status code, also
        when there is no phrase to follow it (:rfc:`9112`, section 4):
        ``"HTTP/1.1 599 "``.

        :param include_body: append a blank line and the body as text: a
                             str as it is, bytes decoded with the
                             Content-Type charset — what is not text in
                             it shows as U+FFFD, nothing raises; a
                             :py:class:`~action0.req.body.BodyProducer` is
                             not consumed — a placeholder is shown instead
        :param separator: the string joining the lines (no trailing one)
        :return: e.g. ``"HTTP/1.1 404 Not Found\r\nContent-Length: 0"``
        :raises ValueError: if the HTTP version or the reason phrase
                            contains a CR, an LF or a NUL — it would
                            start a line of its own
        """
        phrase = self.phrase
        _check_safe(self.http_version, "HTTP version")
        _check_safe(phrase, "reason phrase")
        lines = [f"{self.http_version} {self.status} {phrase}"]
        if self.headers:
            lines.append(self.headers.as_str(separator))
        rendered = separator.join(lines)

        if include_body and self.body is not None:
            body_text = _rendered_body(self.body, _charset(self.headers))
            rendered = f"{rendered}{separator}{separator}{body_text}"
        return rendered

    def __eq__(self, other: object) -> bool:
        """
        Responses are equal when their status, reason, headers, body and
        HTTP version are equal, each with the part's own equality
        semantics (e.g. header name casing doesn't matter). The body is
        compared as set: ``b"x"`` and ``"x"`` are different bodies, and
        producers are equal only if they say so
        (:py:class:`~action0.req.body.BytesBody` compares its bytes; a
        stream is equal to itself alone). The
        ``request`` reference and the :py:attr:`meta` dict are metadata
        and not compared.

        :param other: the Response to compare with
        :return: whether the responses are equal
        """
        if isinstance(other, Response):
            return (
                self.status == other.status
                and self.reason == other.reason
                and self.http_version == other.http_version
                and self.headers == other.headers
                and self.body == other.body
            )
        return NotImplemented

    def __repr__(self) -> str:
        """
        :return: the status code and the :py:attr:`phrase`; headers and
                 body are left out, so no secret header values can leak.
                 ``str()`` falls back to this representation — the wire
                 rendering is only available explicitly via
                 :py:meth:`as_str`.
        """
        inner = f"{self.status} {self.phrase}".rstrip()
        return f"{self.__class__.__name__}({inner})"

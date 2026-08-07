"""Constants for HTTP request methods."""

from enum import StrEnum


class Method(StrEnum):
    """
    The HTTP request methods ("verbs") as string constants.

    The members are the methods defined by :rfc:`9110`, plus ``PATCH``
    (:rfc:`5789`) and ``QUERY`` (:rfc:`10008`). Being a
    :py:class:`enum.StrEnum`, every member *is* the method string:

    >>> from action0.req import Method
    >>> print(Method.GET)
    GET
    >>> Method.GET == "GET"
    True
    >>> Method("POST") is Method.POST
    True
    """

    CONNECT = "CONNECT"
    DELETE = "DELETE"
    GET = "GET"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"
    PATCH = "PATCH"
    POST = "POST"
    PUT = "PUT"
    QUERY = "QUERY"
    TRACE = "TRACE"

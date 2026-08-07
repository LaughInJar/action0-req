import unittest

from action0.req import Method


class MethodTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.request.Method`
    """

    def test_members_are_strings(self) -> None:
        """
        Test that members are real strings usable wherever a str is expected.
        """
        self.assertIsInstance(Method.GET, str)
        self.assertEqual(Method.GET, "GET")
        self.assertEqual(str(Method.GET), "GET")
        self.assertEqual(f"{Method.POST}", "POST")

    def test_lookup_by_value(self) -> None:
        """
        Test that members can be looked up from the method string.
        """
        self.assertIs(Method("POST"), Method.POST)
        with self.assertRaises(ValueError):
            Method("FETCH")

    def test_all_methods(self) -> None:
        """
        Test that exactly the RFC 9110 + PATCH + QUERY methods exist.
        """
        self.assertEqual(
            sorted(member.value for member in Method),
            [
                "CONNECT",
                "DELETE",
                "GET",
                "HEAD",
                "OPTIONS",
                "PATCH",
                "POST",
                "PUT",
                "QUERY",
                "TRACE",
            ],
        )

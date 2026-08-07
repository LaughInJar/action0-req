import unittest

from action0.req import Header


class HeaderTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.headers.Header`
    """

    def test_members_are_strings(self) -> None:
        """
        Test that members are real strings usable wherever a str is expected.
        """
        self.assertIsInstance(Header.CONTENT_TYPE, str)
        self.assertEqual(Header.CONTENT_TYPE, "Content-Type")
        self.assertEqual(str(Header.CONTENT_TYPE), "Content-Type")
        self.assertEqual(f"{Header.CONTENT_TYPE}", "Content-Type")
        self.assertEqual("Accept".join(["<", ">"]), "<" + Header.ACCEPT + ">")

    def test_lookup_by_value(self) -> None:
        """
        Test that members can be looked up from the header name string.
        """
        self.assertIs(Header("ETag"), Header.ETAG)
        self.assertIs(Header("WWW-Authenticate"), Header.WWW_AUTHENTICATE)
        with self.assertRaises(ValueError):
            Header("No-Such-Header")

    def test_common_members(self) -> None:
        """
        Test a sample of members from each registry section.
        """
        # permanent registrations
        self.assertEqual(Header.AUTHORIZATION, "Authorization")
        self.assertEqual(Header.CONTENT_LENGTH, "Content-Length")
        self.assertEqual(Header.SET_COOKIE, "Set-Cookie")
        self.assertEqual(Header.X_FRAME_OPTIONS, "X-Frame-Options")
        # deprecated registrations
        self.assertEqual(Header.PRAGMA, "Pragma")
        self.assertEqual(Header.ACCEPT_CHARSET, "Accept-Charset")
        # provisional registrations
        self.assertEqual(Header.PERMISSIONS_POLICY, "Permissions-Policy")
        # unregistered de-facto standards
        self.assertEqual(Header.X_FORWARDED_FOR, "X-Forwarded-For")
        self.assertEqual(Header.X_REQUEST_ID, "X-Request-Id")

    def test_registry_size(self) -> None:
        """
        Test that the full IANA registry made it into the enum.
        """
        # 187 permanent + 8 deprecated + 23 provisional + 10 de-facto
        self.assertEqual(len(Header), 228)

    def test_values_unique(self) -> None:
        """
        Test that no two members share a header name (which would silently
        turn one of them into an alias).
        """
        values = [member.value for member in Header]
        self.assertEqual(len(values), len(set(values)))

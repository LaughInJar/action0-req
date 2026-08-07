import unittest

from action0.req import Status


class StatusTestCase(unittest.TestCase):
    """
    tests for :py:class:`action0.req.status.Status`
    """

    def test_members_are_ints(self) -> None:
        """
        Test that members are real integers usable wherever an int is expected.
        """
        self.assertIsInstance(Status.OK, int)
        self.assertEqual(Status.OK, 200)
        self.assertEqual(str(Status.OK), "200")
        self.assertEqual(f"{Status.NOT_FOUND}", "404")
        self.assertEqual(Status.NOT_FOUND + 0, 404)

    def test_lookup_by_value(self) -> None:
        """
        Test that members can be looked up from the numeric code.
        """
        self.assertIs(Status(404), Status.NOT_FOUND)
        self.assertIs(Status(511), Status.NETWORK_AUTHENTICATION_REQUIRED)
        with self.assertRaises(ValueError):
            Status(599)

    def test_phrase(self) -> None:
        """
        Test the registered reason phrases.
        """
        self.assertEqual(Status.OK.phrase, "OK")
        self.assertEqual(Status.NOT_FOUND.phrase, "Not Found")
        self.assertEqual(Status.IM_USED.phrase, "IM Used")
        self.assertEqual(Status.IM_A_TEAPOT.phrase, "I'm a Teapot")
        for member in Status:
            self.assertNotEqual(member.phrase, "")

    def test_categories(self) -> None:
        """
        Test the category properties on their boundary codes.
        """
        self.assertTrue(Status.CONTINUE.is_informational)  # 100
        self.assertTrue(Status.OK.is_success)  # 200
        self.assertTrue(Status.PERMANENT_REDIRECT.is_redirection)  # 308
        self.assertTrue(Status.BAD_REQUEST.is_client_error)  # 400
        self.assertTrue(Status.UNAVAILABLE_FOR_LEGAL_REASONS.is_client_error)  # 451
        self.assertTrue(Status.INTERNAL_SERVER_ERROR.is_server_error)  # 500
        self.assertTrue(Status.NETWORK_AUTHENTICATION_REQUIRED.is_server_error)  # 511
        self.assertFalse(Status.OK.is_client_error)

    def test_categories_partition(self) -> None:
        """
        Test that every member falls into exactly one category.
        """
        for member in Status:
            categories = [
                member.is_informational,
                member.is_success,
                member.is_redirection,
                member.is_client_error,
                member.is_server_error,
            ]
            self.assertEqual(categories.count(True), 1, member)

    def test_aliases(self) -> None:
        """
        Test the pre-RFC-9110 reason phrase aliases.
        """
        self.assertIs(Status.PAYLOAD_TOO_LARGE, Status.CONTENT_TOO_LARGE)
        self.assertIs(Status.UNPROCESSABLE_ENTITY, Status.UNPROCESSABLE_CONTENT)
        # aliases don't show up when iterating
        names = [member.name for member in Status]
        self.assertNotIn("PAYLOAD_TOO_LARGE", names)
        self.assertIn("CONTENT_TOO_LARGE", names)

    def test_registry_size(self) -> None:
        """
        Test that the full IANA registry made it into the enum.
        """
        # 4 + 10 + 8 + 29 + 11 codes (not counting the two aliases)
        self.assertEqual(len(Status), 62)

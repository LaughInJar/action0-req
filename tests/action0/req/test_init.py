import re
import unittest

import action0.req


class PackageTestCase(unittest.TestCase):
    """
    tests for the :py:mod:`action0.req` package root
    """

    def test_version(self) -> None:
        """
        Test that the version is a non-empty x.y.z string.
        """
        self.assertRegex(action0.req.__version__, re.compile(r"^\d+\.\d+\.\d+$"))

    def test_action0_url_importable(self) -> None:
        """
        Test that the action0-url dependency resolves inside the same namespace.
        """
        from action0.url import Url

        self.assertEqual(Url("https://example.com/a").path, "/a")

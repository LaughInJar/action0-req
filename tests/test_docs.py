import contextlib
import io
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_ROOT = Path(__file__).resolve().parent.parent

# a fenced Python block of a Markdown page
_BLOCK = re.compile(r"^```python\n(.*?)^```", re.DOTALL | re.MULTILINE)


def _expected(block: str) -> list[str]:
    """
    The output a block promises: its comments — the lines that are one
    ("# 404") and those behind a statement ("print(x)  # 404").

    :param block: the source of the example
    :return: the texts of the comments, in order
    """
    comments: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            comments.append(stripped[1:].strip())
        elif "  # " in stripped:
            comments.append(stripped.split("  # ", 1)[1].strip())
    return comments


def _unexpected(block: str, name: str) -> list[str]:
    """
    Run an example and compare what it prints with its comments: each
    printed line has to be there, in order. (Not every comment is output
    — some just explain —, so the comments may say more than the block
    prints, never something else.)

    :param block: the source of the example
    :param name: what to call the example in a traceback
    :return: the printed lines its comments do not show (at their
             position); empty if the example keeps its promise
    """
    output = io.StringIO()
    # in a directory of its own: an example may write a file
    with TemporaryDirectory() as tmp, contextlib.chdir(tmp):
        with contextlib.redirect_stdout(output):
            exec(compile(block, name, "exec"), {})
    comments = iter(_expected(block))
    return [
        printed
        for printed in output.getvalue().splitlines()
        if not any(printed.strip() == comment for comment in comments)
    ]


class DocsExamplesTestCase(unittest.TestCase):
    """
    The examples of the guide and the README are promised to be runnable
    as they are, with the exact output shown in their comments. This runs
    them, so they cannot go stale.
    """

    def _check(self, page: str) -> None:
        """
        Run every Python block of a page, see :py:func:`_unexpected`.

        :param page: the path of the Markdown file, relative to the
                     project root
        """
        blocks = _BLOCK.findall((_ROOT / page).read_text(encoding="utf-8"))
        self.assertTrue(blocks, f"no examples found in {page}")
        for number, block in enumerate(blocks, start=1):
            with self.subTest(page=page, block=number, starts=block.splitlines()[0]):
                self.assertEqual(
                    _unexpected(block, f"{page}:block {number}"),
                    [],
                    f"the example prints what its comments do not show:\n{block}",
                )

    def test_guide(self) -> None:
        """
        Test the examples of the guide.
        """
        self._check("docs/usage.md")

    def test_readme(self) -> None:
        """
        Test the examples of the README.
        """
        self._check("README.md")

    def test_a_wrong_comment_is_noticed(self) -> None:
        """
        Test the test: an example whose comment shows something else
        than it prints, or shows it in another order, is found out.
        """
        self.assertEqual(
            _expected("print(1)  # 1\n# two\nx = 3\n    # four"), ["1", "two", "four"]
        )
        self.assertEqual(_unexpected("print(1 + 1)\n# 2", "example"), [])
        self.assertEqual(_unexpected("print(1 + 1)\n# 3", "example"), ["2"])
        self.assertEqual(_unexpected("print(1)\nprint(2)\n# 2\n# 1", "example"), ["2"])
        self.assertEqual(_unexpected("x = 1  # no output, just a remark", "example"), [])

from __future__ import annotations

import py_compile
import unittest
from pathlib import Path


class TestLegacyReplModule(unittest.TestCase):
    def test_legacy_cli_repl_py_is_valid_python(self) -> None:
        # Validate the lazy facade src/repl/core.py is valid Python.
        repl_path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "repl"
            / "core.py"
        )
        py_compile.compile(str(repl_path), doraise=True)


if __name__ == "__main__":
    unittest.main()

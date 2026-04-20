from __future__ import annotations

import py_compile
import unittest
from pathlib import Path


class TestLegacyReplModule(unittest.TestCase):
    def test_src_repl_py_is_valid_python(self) -> None:
        repl_path = Path(__file__).resolve().parents[1] / "src" / "repl.py"
        py_compile.compile(str(repl_path), doraise=True)


if __name__ == "__main__":
    unittest.main()

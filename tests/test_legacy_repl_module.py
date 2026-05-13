from __future__ import annotations

import py_compile
import unittest
from pathlib import Path


class TestLegacyReplModule(unittest.TestCase):
    def test_legacy_cli_repl_py_is_valid_python(self) -> None:
        # ch01 round-2 P3: src/repl.py was relocated to
        # scripts/audit/legacy_cli_repl.py.
        repl_path = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "audit"
            / "legacy_cli_repl.py"
        )
        py_compile.compile(str(repl_path), doraise=True)


if __name__ == "__main__":
    unittest.main()

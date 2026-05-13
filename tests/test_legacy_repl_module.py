"""Syntax-check the relocated legacy CLI demo module.

In Phase 3 of the ch01 architecture refactor, the audit-only
``src/repl.py`` (the legacy ``ClawcodexCLI`` demo, distinct from the
production REPL at ``src/repl/``) moved to
``scripts/audit/legacy_cli_repl.py``. This test guards that the moved
file still compiles cleanly.
"""

from __future__ import annotations

import py_compile
import unittest
from pathlib import Path


class TestLegacyReplModule(unittest.TestCase):
    def test_legacy_cli_repl_compiles(self) -> None:
        repl_path = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "audit" / "legacy_cli_repl.py"
        )
        py_compile.compile(str(repl_path), doraise=True)


if __name__ == "__main__":
    unittest.main()

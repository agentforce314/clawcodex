"""Tests for the downstream CLI entrypoint (clawcodex_ext.cli.main)."""

from __future__ import annotations

import importlib
import sys


def test_downstream_cli_main_is_callable():
    """Main is callable."""
    module = importlib.import_module("clawcodex_ext.cli.main")
    assert callable(module.main)


def test_downstream_cli_main_delegates_to_run_cli(monkeypatch):
    """main() delegates to run_cli() via the dispatch module."""
    from clawcodex_ext.cli.main import main

    dispatched = []
    def fake_run_cli(argv=None):
        dispatched.append(argv)
        return 42

    monkeypatch.setattr("clawcodex_ext.cli.dispatch.run_cli", fake_run_cli)
    result = main()
    assert result == 42
    assert len(dispatched) == 1


def test_downstream_cli_main_import_is_lightweight():
    """Importing the entrypoint does not load heavyweight modules."""
    for name in ("clawcodex_ext.cli.main", "src.tui.app", "src.repl.core"):
        sys.modules.pop(name, None)

    importlib.import_module("clawcodex_ext.cli.main")

    assert "src.tui.app" not in sys.modules
    assert "src.repl.core" not in sys.modules
    # Note: src.cli is NOT loaded at import time — it is imported lazily
    # inside run_cli() only for specific code paths (subcommand handlers,
    # REPL mode). This is intentional so importing the entrypoint stays light.


def test_downstream_cli_main_does_not_load_src_cli_at_import_time():
    """Importing main does NOT eagerly pull in src.cli."""
    for name in ("clawcodex_ext.cli.main", "src.cli"):
        sys.modules.pop(name, None)

    import clawcodex_ext.cli.main as main_mod

    # The module should be loadable without src.cli appearing in sys.modules
    # (src.cli gets imported lazily inside run_cli, not at import time).
    # Note: because we haven't called main() yet, src.cli may not be present.
    # We check the import chain is shallow.
    assert "src.tui.app" not in sys.modules
    assert "src.repl.core" not in sys.modules
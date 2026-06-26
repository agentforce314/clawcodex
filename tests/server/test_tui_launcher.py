"""Tests for `clawcodex tui` (src/entrypoints/tui_launcher.py).

The launcher starts the agent-server in-process and spawns the TUI as a child.
We don't need a real Ink TTY: `CLAWCODEX_TUI_CMD` overrides the child command,
so we point it at a tiny stdlib HTTP client that proves the spawned child can
reach + authenticate to the auto-started server, then the launcher tears down.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from src.entrypoints.tui_launcher import _resolve_tui_dir, run_tui_launcher


pytestmark = pytest.mark.integration


def test_resolve_tui_dir_finds_repo_client():
    """Auto-detect locates the sibling ui-tui/ via the upward walk."""
    found = _resolve_tui_dir(None)
    assert found is not None, "expected to find ui-tui by walking up from the package"
    assert found.name == "ui-tui"
    assert (found / "src" / "cli.tsx").exists()


def test_launcher_spawns_child_against_live_server(tmp_path, monkeypatch):
    # Isolate the session index away from the real ~/.clawcodex.
    monkeypatch.setenv("HOME", str(tmp_path))

    # A stdlib-only "TUI": POST /sessions to the cc:// URL with the token the
    # launcher passes, exit 0 iff it gets a 201 (server up + token accepted).
    child = tmp_path / "fake_tui.py"
    child.write_text(textwrap.dedent(
        """
        import json, sys, urllib.request
        cc = sys.argv[1]
        token = sys.argv[sys.argv.index("--token") + 1]
        http = "http://" + cc[len("cc://"):]
        req = urllib.request.Request(
            http + "/sessions",
            data=json.dumps({"cwd": "/tmp"}).encode(),
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {token}"},
            method="POST",
        )
        try:
            r = urllib.request.urlopen(req, timeout=5)
            sys.exit(0 if r.status == 201 else 3)
        except Exception:
            sys.exit(4)
        """
    ))
    monkeypatch.setenv("CLAWCODEX_TUI_CMD", f"{sys.executable} {child}")

    rc = run_tui_launcher(["--workspace", str(tmp_path)])
    assert rc == 0, "launcher should start the server, spawn the child, and return its exit code"

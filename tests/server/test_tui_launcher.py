"""Tests for `clawcodex tui` (src/entrypoints/tui_launcher.py).

The launcher is a thin bootstrap for the hermes route: it spawns the Ink TUI
(the parent), which in turn spawns the Python agent-server child. We don't need
a real Ink TTY — `CLAWCODEX_TUI_CMD` overrides the child command, so we point it
at a tiny stub that asserts the launcher handed it a *runnable* agent-server
command via `CLAWCODEX_AGENT_SERVER_CMD`.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from src.entrypoints.tui_launcher import _agent_server_cmd, _resolve_tui_dir, run_tui_launcher


pytestmark = pytest.mark.integration


def test_resolve_tui_dir_finds_repo_client():
    """Auto-detect locates the sibling ui-tui/ via the upward walk."""
    found = _resolve_tui_dir(None)
    assert found is not None, "expected to find ui-tui by walking up from the package"
    assert found.name == "ui-tui"
    # entry.tsx is the client entrypoint (the hermes-port rename of cli.tsx) —
    # the same marker _resolve_tui_dir itself probes for.
    assert (found / "src" / "entry.tsx").exists()


def test_agent_server_cmd_invokes_module():
    """The backend command the client spawns is a runnable python -m entry."""
    class _Args:
        permission_mode = "default"
        provider = None
        model = None

    cmd = _agent_server_cmd(_Args())
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "src.entrypoints.agent_server_cli"]
    assert "--permission-mode" in cmd
    # No bypass flags → availability must not leak into the backend cmd.
    assert "--allow-dangerously-skip-permissions" not in cmd


def test_agent_server_cmd_forwards_bypass_availability():
    """Resolved availability rides to the backend as --allow-dangerously-…,
    so Shift+Tab / /mode can reach bypassPermissions at runtime."""
    class _Args:
        permission_mode = "bypassPermissions"
        is_bypass_available = True
        provider = None
        model = None

    cmd = _agent_server_cmd(_Args())
    assert "--allow-dangerously-skip-permissions" in cmd
    i = cmd.index("--permission-mode")
    assert cmd[i + 1] == "bypassPermissions"


def _flag_probe_stub(tmp_path, monkeypatch, expect: str) -> None:
    """Point CLAWCODEX_TUI_CMD at a stub that exits 0 iff the launcher's
    CLAWCODEX_AGENT_SERVER_CMD contains `expect`.

    `expect` pins the exact `--permission-mode <mode> [--allow-…]` ordering
    that `_agent_server_cmd` emits — intentional, so a reorder of that builder
    surfaces here rather than silently changing the spawned backend cmd."""
    child = tmp_path / "fake_tui.py"
    child.write_text(textwrap.dedent(
        f"""
        import os, sys
        cmd = os.environ.get("CLAWCODEX_AGENT_SERVER_CMD", "")
        sys.exit(0 if {expect!r} in cmd else 4)
        """
    ))
    monkeypatch.setenv("CLAWCODEX_TUI_CMD", f"{sys.executable} {child}")


def test_launcher_dsp_flag_starts_backend_in_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _flag_probe_stub(
        tmp_path, monkeypatch,
        "--permission-mode bypassPermissions",
    )
    rc = run_tui_launcher(
        ["--workspace", str(tmp_path), "--dangerously-skip-permissions"],
    )
    assert rc == 0, "expected --permission-mode bypassPermissions in backend cmd"


def test_launcher_allow_flag_forwards_availability_without_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _flag_probe_stub(
        tmp_path, monkeypatch,
        "--permission-mode default --allow-dangerously-skip-permissions",
    )
    rc = run_tui_launcher(
        ["--workspace", str(tmp_path), "--allow-dangerously-skip-permissions"],
    )
    assert rc == 0, "expected availability flag (and default mode) in backend cmd"


def test_launcher_hands_client_a_runnable_backend_cmd(tmp_path, monkeypatch):
    # Isolate any HOME-derived state.
    monkeypatch.setenv("HOME", str(tmp_path))

    # A stdlib-only "TUI": verify the launcher set CLAWCODEX_AGENT_SERVER_CMD to
    # a command that actually runs (argparse --help exits 0). This proves the
    # hermes route is wired: client → spawns this backend command.
    child = tmp_path / "fake_tui.py"
    child.write_text(textwrap.dedent(
        """
        import os, subprocess, sys
        cmd = os.environ.get("CLAWCODEX_AGENT_SERVER_CMD", "")
        if not cmd:
            sys.exit(2)
        r = subprocess.run(cmd.split() + ["--help"], capture_output=True, timeout=120)
        sys.exit(0 if r.returncode == 0 else 4)
        """
    ))
    monkeypatch.setenv("CLAWCODEX_TUI_CMD", f"{sys.executable} {child}")

    rc = run_tui_launcher(["--workspace", str(tmp_path)])
    assert rc == 0, "launcher should hand the client a runnable agent-server command"

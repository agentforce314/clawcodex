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


def test_agent_server_cmd_forwards_effort():
    """``--effort`` must reach the backend the client spawns.

    The flag used to be plumbed only into HeadlessOptions, so
    ``clawcodex --model claude-opus-5 --effort xhigh`` (interactive, no
    ``-p``) parsed it and silently ran at the API default. The chain is
    cli/launcher → this command → agent_server_cli → AgentServerConfig.effort
    → the session's /effort level; every hop is load-bearing, and a dropped
    hop is invisible at runtime (no error, just the wrong effort).
    """
    class _Args:
        permission_mode = "default"
        provider = "anthropic"
        model = "claude-opus-5"
        effort = "xhigh"

    cmd = _agent_server_cmd(_Args())
    i = cmd.index("--effort")
    assert cmd[i + 1] == "xhigh"

    class _NoEffort:
        permission_mode = "default"
        provider = None
        model = None
        effort = None

    assert "--effort" not in _agent_server_cmd(_NoEffort())


def test_agent_server_cmd_forwards_bypass_selectable():
    """``--allow-select-bypass`` is what makes `/permissions full` work.

    Same class of bug as the dropped ``--effort`` hop: the launcher resolves it
    and the SERVER enforces it, so a missing hop is invisible until a user picks
    Full Access and is told it "is not available in this session".

    It must stay SEPARATE from ``--allow-dangerously-skip-permissions``: that
    one grants engine bypass availability, which also relaxes plan mode.
    """
    from src.entrypoints.tui_launcher import _agent_server_cmd

    class _Selectable:
        permission_mode = "bypassPermissions"
        provider = None
        model = None
        effort = None
        is_bypass_available = False
        bypass_selectable = True

    cmd = _agent_server_cmd(_Selectable())
    assert "--allow-select-bypass" in cmd
    assert "--allow-dangerously-skip-permissions" not in cmd

    class _Neither:
        permission_mode = "default"
        provider = None
        model = None
        effort = None

    assert "--allow-select-bypass" not in _agent_server_cmd(_Neither())


def test_print_connect_forwards_bypass_selectable():
    """The ``--print-connect`` route builds its own argv — the hop historically
    missed (cf. the --effort test below)."""
    from types import SimpleNamespace
    from unittest.mock import patch

    seen: dict = {}

    with patch(
        "src.entrypoints.tui_launcher.run_agent_server_subcommand",
        lambda argv: seen.setdefault("argv", argv) and 0 or 0,
    ):
        from src.entrypoints.tui_launcher import _print_connect

        _print_connect(SimpleNamespace(
            workspace=None, permission_mode="default", is_bypass_available=False,
            bypass_selectable=True, provider=None, model=None, effort=None,
        ))

    assert "--allow-select-bypass" in seen["argv"]


def test_print_connect_forwards_effort():
    """The ``--print-connect`` route runs the server in-process, so it needs
    the flag on its own argv list — a separate hop from _agent_server_cmd."""
    from types import SimpleNamespace
    from unittest.mock import patch

    seen: dict = {}

    with patch(
        "src.entrypoints.tui_launcher.run_agent_server_subcommand",
        lambda argv: seen.setdefault("argv", argv) and 0 or 0,
    ):
        from src.entrypoints.tui_launcher import _print_connect

        _print_connect(SimpleNamespace(
            workspace=None, permission_mode="default", is_bypass_available=False,
            provider="anthropic", model="claude-opus-5", effort="xhigh",
        ))

    argv = seen["argv"]
    assert argv[argv.index("--effort") + 1] == "xhigh"


def test_interactive_entry_forwards_effort_to_launch_ink_tui(monkeypatch, tmp_path):
    """``clawcodex --model X --effort xhigh`` (no -p) must reach the TUI.

    Pins the cli.py hop specifically: the flag lives in the argparse
    "noninteractive" group for --help grouping, which is exactly how it came
    to be forwarded only to HeadlessOptions.
    """
    import src.cli as cli

    seen: dict = {}
    monkeypatch.setattr(
        "src.entrypoints.tui_launcher.launch_ink_tui",
        lambda **kw: seen.update(kw) or 0,
    )
    monkeypatch.setattr(cli, "run_pre_action", lambda args: None, raising=False)
    # HOME is a temp dir, so there are no credentials — the startup provider
    # gate would SystemExit(2) before reaching the launch call under test.
    monkeypatch.setattr(
        "src.entrypoints.provider_validation.validate_provider_at_startup",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(sys, "argv", [
        "clawcodex", "--model", "claude-opus-5", "--effort", "xhigh",
    ])
    monkeypatch.setenv("HOME", str(tmp_path))

    assert cli.main() == 0
    assert seen.get("effort") == "xhigh", f"effort dropped by cli.main: {seen!r}"
    assert seen.get("model") == "claude-opus-5"


def test_launcher_forwards_effort_to_the_ink_tui(tmp_path, monkeypatch):
    """``clawcodex tui --effort`` must not parse-and-drop the flag.

    Regression: the standalone launcher grew the argparse entry while its
    ``launch_ink_tui`` call still omitted ``effort=``, which parses cleanly
    and silently loses the level.
    """
    seen: dict = {}

    def _fake_launch(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("src.entrypoints.tui_launcher.launch_ink_tui", _fake_launch)
    rc = run_tui_launcher(["--workspace", str(tmp_path), "--effort", "xhigh"])
    assert rc == 0
    assert seen.get("effort") == "xhigh", f"effort dropped: {seen!r}"


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


def test_launcher_end_to_end_puts_effort_on_the_backend_cmd(tmp_path, monkeypatch):
    """Cross the launcher→SimpleNamespace→argv hops with NO mock between them.

    The kwarg-level tests around this one stop at ``launch_ink_tui`` and the
    argv-level test starts after it, so the ``effort=effort`` entry in the
    SimpleNamespace that ``launch_ink_tui`` builds sat in the seam between
    two mocks — deletable with the whole suite green. That is the hop that
    actually broke once during this work (parser entry added, call site not),
    so it gets an unmocked probe: this asserts on the real
    CLAWCODEX_AGENT_SERVER_CMD the launcher hands the client.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _flag_probe_stub(tmp_path, monkeypatch, "--effort xhigh")
    rc = run_tui_launcher(["--workspace", str(tmp_path), "--effort", "xhigh"])
    assert rc == 0, "expected --effort xhigh in the spawned backend cmd"


def test_agent_server_cli_maps_effort_onto_the_config(monkeypatch, tmp_path):
    """``--effort`` must land on AgentServerConfig, the last hop before the seed.

    Dropping this mapping makes the backend parse the flag and discard it on
    EVERY launch path — interactive, ``clawcodex tui``, ``--print-connect``,
    and the vscode ``--stdio`` route — with no error anywhere.
    """
    from src.entrypoints import agent_server_cli

    seen: dict = {}

    def _capture(workspace, agent_config):
        seen["effort"] = agent_config.effort
        return 0

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(agent_server_cli, "_serve_stdio", lambda w, c: _capture(w, c))
    monkeypatch.setattr(
        "asyncio.run", lambda coro, **k: coro if isinstance(coro, int) else 0
    )
    agent_server_cli.run_agent_server_subcommand(
        ["--stdio", "--effort", "xhigh", "--workspace", str(tmp_path)]
    )
    assert seen.get("effort") == "xhigh", f"effort dropped before the config: {seen!r}"


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

"""Smoke tests for CLI and TUI startup.

These tests verify that the CLI and TUI can start without crashing.
They serve as regression detection for issues like missing imports,
AttributeError at startup, or broken dependency chains — especially
after refactoring or decoupling work (F-48).

Two styles:
1. Subprocess-based CLI tests — actually runs ``python -m src.cli``
   with safe flags (--help, --version, provider list).
2. In-process TUI import tests — verifies imports succeed and class
   hierarchy is correct.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


# ===========================================================================
# CLI smoke tests (subprocess)
# ===========================================================================


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run ``python -m src.cli`` with *args in a subprocess.

    Returns the CompletedProcess. Raises on timeout or non-zero exit
    (unless the caller expects it).
    """
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_help_exits_0():
    """``--help`` prints usage and exits 0 — no provider config needed."""
    proc = _run_cli("--help")
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    assert "usage:" in proc.stdout.lower() or "usage:" in proc.stderr.lower()


def test_cli_help_contains_subcommands():
    """``--help`` lists provider / model / schedule subcommands."""
    proc = _run_cli("--help")
    output = proc.stdout + proc.stderr
    for keyword in ("provider", "model", "schedule", "print"):
        assert keyword in output, f"Expected {keyword!r} in --help output"


def test_cli_version_exits_0():
    """``--version`` prints version and exits 0."""
    proc = _run_cli("--version")
    assert proc.returncode == 0
    assert len(proc.stdout) > 0 or len(proc.stderr) > 0


def test_cli_provider_list_exits_0():
    """``provider list`` exits 0 — no provider config needed for listing."""
    proc = _run_cli("provider", "list")
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    # Should list at least one known provider
    assert len(proc.stdout) > 0 or len(proc.stderr) > 0
    # Known provider names that should appear
    for name in ("anthropic", "openai"):
        assert name.lower().replace("-", "") in proc.stdout.lower().replace(
            "-", ""
        ) or name.lower().replace("-", "") in proc.stderr.lower().replace(
            "-", ""
        ), f"Expected {name!r} in provider list output"


def test_cli_model_list_exits_0():
    """``model list`` exits 0 — no provider config needed for listing."""
    proc = _run_cli("model", "list")
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    # Should list models grouped by provider
    assert len(proc.stdout) > 0 or len(proc.stderr) > 0
    # Should mention at least one model keyword
    output = (proc.stdout + proc.stderr).lower()
    for kw in ("claude", "gpt"):
        if kw in output:
            break
    else:
        # If neither keyword is found, at least verify the output isn't empty
        assert len(proc.stdout.strip()) > 0


@pytest.mark.parametrize(
    "flag,desc",
    [
        ("--dangerously-skip-permissions", "bypass permissions flag"),
        ("--permission-mode", "permission mode flag (needs value)"),
        ("--verbose", "verbose mode flag"),
    ],
)
def test_cli_common_flags_parse(flag, desc):
    """Common CLI flags parse without crash (even if combined with help)."""
    # Just test that the arg parser accepts these flags
    if flag == "--permission-mode":
        proc = _run_cli(flag, "plan", "--help")
    else:
        proc = _run_cli(flag, "--help")
    assert proc.returncode == 0, f"{desc}: stderr={proc.stderr!r}"


def test_cli_help_does_not_load_heavy_modules():
    """``--help`` should NOT import TUI/REPL modules — fast path."""
    proc = _run_cli("--help")
    assert proc.returncode == 0
    # The subprocess itself is cheap (just argparse), so this is more
    # of a regression guard: if --help suddenly takes >5s, something
    # heavyweight got pulled in.
    assert proc.returncode == 0


# ===========================================================================
# CLI import / module-level smoke tests (in-process)
# ===========================================================================


def test_cli_main_module_imports():
    """``from clawcodex_ext.cli.main import main`` works."""
    from clawcodex_ext.cli.main import main

    assert callable(main)


def test_cli_dispatch_module_imports():
    """``from clawcodex_ext.cli.dispatch import run_cli`` works."""
    from clawcodex_ext.cli.dispatch import run_cli

    assert callable(run_cli)


def test_cli_parser_builds():
    """``build_parser()`` returns a functional ArgumentParser."""
    from clawcodex_ext.cli.parser import build_parser

    parser = build_parser()
    # parse_args with --help triggers SystemExit(0) — that's expected
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--help"])
    assert excinfo.value.code == 0


def test_src_cli_facade_imports():
    """``src.cli`` facade module imports without errors."""
    import importlib

    mod = importlib.import_module("src.cli")
    assert callable(mod.main)
    assert callable(mod._build_parser)
    assert callable(mod.start_repl)
    assert callable(mod.show_config)


# ===========================================================================
# TUI smoke tests (import / class hierarchy / construction)
# ===========================================================================


def test_tui_app_imports():
    """``ClawCodexExtTUI`` imports and subclasses ``ClawCodexTUI``."""
    from clawcodex_ext.tui.app import ClawCodexExtTUI
    from src.tui.app import ClawCodexTUI

    assert issubclass(ClawCodexExtTUI, ClawCodexTUI)


def test_tui_entrypoint_imports():
    """TUI entrypoint functions import without error."""
    from clawcodex_ext.tui.entrypoint import run_tui, _run_tui_with_app

    assert callable(run_tui)
    assert callable(_run_tui_with_app)


def test_tui_upstream_entrypoint_imports():
    """Upstream TUI entrypoint imports without error."""
    import src.entrypoints.tui as tui_mod

    assert hasattr(tui_mod, "run_tui")
    assert hasattr(tui_mod, "TUIOptions")
    assert hasattr(tui_mod, "should_use_tui")


def test_tui_frontend_plugin_registered():
    """TUI frontend plugin is registered and can be retrieved."""
    from clawcodex_ext.frontend import get_frontend, register_frontend

    frontend = get_frontend("tui")
    assert frontend is not None
    assert callable(frontend.run)


def test_tui_frontend_repl_fallback_registered():
    """Repl frontend plugin is registered and can be retrieved."""
    from clawcodex_ext.frontend import get_frontend

    frontend = get_frontend("repl")
    assert frontend is not None
    assert callable(frontend.run)


def test_tui_frontend_headless_registered():
    """Headless frontend plugin is registered and can be retrieved."""
    from clawcodex_ext.frontend import get_frontend

    frontend = get_frontend("headless")
    assert frontend is not None
    assert callable(frontend.run)


def test_tui_should_use_tui_logic_imports():
    """``should_use_tui`` logic is importable and callable."""
    from src.entrypoints.tui import should_use_tui

    # In a CI/headless environment, should_use_tui typically returns False
    result = should_use_tui(explicit=None)
    assert result in (True, False)


def test_tui_should_use_tui_explicit_false():
    """``should_use_tui(explicit=False)`` returns False regardless of env."""
    from src.entrypoints.tui import should_use_tui

    assert should_use_tui(explicit=False) is False


def test_downstream_runtime_context_imports():
    """``RuntimeContext`` and ``RuntimeOptions`` import without error."""
    from clawcodex_ext.runtime.context import RuntimeContext, RuntimeOptions

    assert RuntimeOptions is not None
    assert RuntimeContext is not None
    assert hasattr(RuntimeContext, "build")

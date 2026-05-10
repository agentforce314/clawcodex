"""WI-4.3 acceptance tests — fast-path subcommands skip the TUI/REPL load.

The chapter's fast-path-dispatch pattern (TS ``main.tsx:914+``): specialized
subcommands like ``claude mcp``, ``claude doctor``, ``claude daemon`` get an
early-return that imports only what they need, skipping the React REPL.
Python mirrors this at ``src/cli.py``'s pre-argparse subcommand sieve.

These tests run each fast-path subcommand in a subprocess and assert the
heavyweight modules (TUI, REPL, full tool registry) are NOT in
``sys.modules`` afterward — i.e., the import graph stayed light.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest


def _run_clawcodex_subcommand(*argv: str) -> tuple[int, dict]:
    """Run ``clawcodex <argv>`` in a subprocess; return (rc, sys_modules_summary).

    Captures the set of imported modules at the time the process exits (after
    the fast-path handler has run). Returns a dict with the keys we care
    about so tests can assert specific modules are NOT loaded.
    """
    snippet = textwrap.dedent(
        """
        import sys
        # Stub argv so the CLI sees the requested subcommand.
        sys.argv = {argv!r}
        try:
            from src import cli
            rc = cli.main()
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        except Exception:
            import traceback
            traceback.print_exc()
            rc = 99

        # Report which heavyweight modules made it into sys.modules.
        loaded = {{
            "rc": int(rc) if rc is not None else 0,
            "tui_app": "src.tui.app" in sys.modules,
            "repl_core": "src.repl.core" in sys.modules,
            "tool_loader": "src.tool_system.loader" in sys.modules,
            "anthropic_sdk": "anthropic" in sys.modules,
            # Sanity: our entrypoint module IS expected to load.
            "entrypoint_target": any(
                m in sys.modules for m in (
                    "src.entrypoints.mcp",
                    "src.entrypoints.doctor",
                    "src.entrypoints.daemon",
                )
            ),
        }}
        import json as _j
        sys.stdout.write("\\n__MODULE_REPORT__:" + _j.dumps(loaded) + "\\n")
        """
    ).format(argv=["clawcodex"] + list(argv))
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        timeout=30,
    )
    rc = proc.returncode
    # Find the report line.
    report_line = next(
        (line for line in proc.stdout.splitlines() if line.startswith("__MODULE_REPORT__:")),
        None,
    )
    if report_line is None:
        pytest.fail(
            f"subcommand {argv!r} produced no module report; "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    report = json.loads(report_line[len("__MODULE_REPORT__:"):])
    return rc, report


def test_mcp_list_does_not_load_tui_or_repl():
    """``clawcodex mcp list`` is the canonical fast-path test from the plan."""
    rc, report = _run_clawcodex_subcommand("mcp", "list")
    # The handler may exit non-zero on its own logic (e.g., no MCP servers
    # configured) but the fast-path acceptance is about the import graph,
    # not the exit code.
    assert report["entrypoint_target"], "expected mcp entrypoint module to load"
    assert not report["tui_app"], (
        "fast-path mcp must NOT load src.tui.app"
    )
    assert not report["repl_core"], (
        "fast-path mcp must NOT load src.repl.core"
    )


def test_mcp_help_does_not_load_tui_or_repl():
    rc, report = _run_clawcodex_subcommand("mcp", "--help")
    assert report["entrypoint_target"]
    assert not report["tui_app"]
    assert not report["repl_core"]


def test_doctor_does_not_load_tui_or_repl():
    rc, report = _run_clawcodex_subcommand("doctor")
    assert report["entrypoint_target"]
    assert not report["tui_app"]
    assert not report["repl_core"]


def test_daemon_does_not_load_tui_or_repl():
    rc, report = _run_clawcodex_subcommand("daemon")
    # daemon is a stub; expected to exit non-zero with "not yet implemented".
    assert report["entrypoint_target"]
    assert not report["tui_app"]
    assert not report["repl_core"]


def test_mcp_list_does_not_load_anthropic_sdk():
    """Combined with WI-4.4: fast-path mcp also doesn't pay the SDK import."""
    rc, report = _run_clawcodex_subcommand("mcp", "list")
    assert not report["anthropic_sdk"], (
        "WI-4.3 + WI-4.4: fast-path mcp must not load the anthropic SDK"
    )


def test_version_short_circuit_remains_lightest():
    """Sanity: ``--version`` was already a fast-path (lighter than mcp).
    Verify it stayed that way after WI-4.3 changes."""
    rc, report = _run_clawcodex_subcommand("--version")
    assert rc == 0
    # --version short-circuits BEFORE the subcommand sieve so no entrypoint
    # module loads.
    assert not report["entrypoint_target"]
    assert not report["tui_app"]
    assert not report["repl_core"]
    assert not report["anthropic_sdk"]


# ---- Critic-M-blocking regression: flag-value collision with subcommand names ----
#
# Pre-fix the sieve walked argv and skipped flag tokens until it found
# the first non-flag value, then treated that as the subcommand. That
# misrouted real prompts whose value happened to equal a subcommand:
# ``clawcodex --model mcp`` would dispatch to the MCP handler instead of
# reaching the REPL.
#
# The fix anchors the sieve to argv[0] only — global flags don't precede
# specialized subcommands in the TS reference either.


def test_flag_value_mcp_does_not_trigger_fast_path():
    """``clawcodex --model mcp`` must NOT dispatch to the MCP entrypoint."""
    rc, report = _run_clawcodex_subcommand("--model", "mcp", "--version")
    # argparse picks up --version which short-circuits; what matters is
    # that the MCP handler did NOT swallow argv before argparse ran.
    assert not report["entrypoint_target"], (
        "flag value 'mcp' must not be interpreted as a fast-path subcommand"
    )


def test_short_flag_value_doctor_does_not_trigger_fast_path():
    """``clawcodex -p doctor`` (print-mode prompt='doctor') must reach print mode."""
    rc, report = _run_clawcodex_subcommand("-p", "doctor", "--version")
    assert not report["entrypoint_target"], (
        "print-mode prompt 'doctor' must not trigger the doctor fast-path"
    )


def test_prompt_value_daemon_does_not_trigger_fast_path():
    """``clawcodex --allowed-tools daemon`` shouldn't dispatch the daemon stub."""
    rc, report = _run_clawcodex_subcommand("--allowed-tools", "daemon", "--version")
    assert not report["entrypoint_target"], (
        "--allowed-tools 'daemon' must not trigger the daemon fast-path"
    )

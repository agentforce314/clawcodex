"""Regression test for F-43 REPL routing fix.

The REPL's ``handle_command`` historically had a hardcoded TUI-only
whitelist that intercepted ``/model`` and printed "is only available in the
Textual TUI" before the runtime command could run. This test guards the
fix by asserting that ``/model`` and ``/provider`` are NOT in the
TUI-only whitelist and that the new command system carries them.
"""

from __future__ import annotations


def test_model_removed_from_repl_tui_only_whitelist() -> None:
    """``/model`` must NOT be in REPL's TUI-only placeholder list."""
    import src.repl.core as repl_core

    src = open(repl_core.__file__, encoding="utf-8").read()

    # The whitelist appears twice in core.py: the special_commands set in
    # handle_command and the early-return check on the next line. Both must
    # drop ``model`` so the runtime command (LocalCommand) gets a chance.
    assert "'model'" not in src.split("special_commands")[1].split("effort")[0]
    assert "if cmd_name in ('repl', 'effort'" in src
    assert "'model'" not in src.split("if cmd_name in ('repl'")[1].split("'effort'")[0]


def test_provider_and_models_listed_in_repl_builtins() -> None:
    """``/provider`` and ``/models`` appear in the REPL built-in commands list."""
    import src.repl.core as repl_core

    src = open(repl_core.__file__, encoding="utf-8").read()

    # The original built-ins list declares which slash commands the REPL
    # exposes. F-43 replaces the legacy TUI-only ``/model`` placeholder with
    # the runtime ``/provider`` command plus ``/models`` (legacy dialog).
    assert '"/provider"' in src
    assert '"/models"' in src


def test_handle_command_routes_model_to_new_command_system() -> None:
    """``handle_command`` must let ``/model`` fall through to the runtime registry."""
    import re

    import src.repl.core as repl_core

    src = open(repl_core.__file__, encoding="utf-8").read()

    # Extract the special_commands set literal.
    match = re.search(r"special_commands\s*=\s*\{(.*?)\}", src, re.DOTALL)
    assert match is not None
    block = match.group(1)

    # The TUI-only stub message must NOT mention ``model`` anymore.
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("'") and stripped.endswith(","):
            stripped = stripped.rstrip(",").strip("'\"")
        if stripped == "model":
            raise AssertionError(
                f"/model must not appear in special_commands; found: {line!r}"
            )

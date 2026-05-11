"""REPL launcher shims.

Mirrors the role of ``typescript/src/replLauncher.tsx`` in the Python
port: a thin factory that decides how to boot the interactive REPL.

**Current default**: the legacy ``prompt_toolkit + rich`` REPL at
:mod:`src.repl.core`. The Textual TUI at :mod:`src.tui.app` is **opt-in**
via the ``--tui`` flag, the ``CLAWCODEX_TUI=1`` environment variable, or
the ``/tui`` slash command from within the legacy REPL. See
:func:`src.entrypoints.tui.should_use_tui` for the policy.

This module intentionally stays slim: the actual wiring lives in
:mod:`src.entrypoints.tui` (Textual path) and :mod:`src.repl.core`
(legacy path). Keeping a dedicated entrypoint here makes it easy for
embedders (e.g. future ``replLauncher`` callers in the ``demos/`` tree)
to pick a UI without reaching into the CLI module.

Composition diagram (entry-point relationships)::

                        cli.main()
                           |
                   +-------+--------+
                   v                v
      cli._run_tui_mode (--tui)   cli.start_repl (default)
                   |                |
                   v                v
       entrypoints.tui.run_tui   repl.core.PromptSession
                   |                ^
                   v                | (slash command /tui handoff)
            tui.app.ClawCodexTUI ---+

The handoff arrow is one-way: ``/tui`` from inside the legacy REPL boots
the Textual UI and returns to the shell when the user exits the TUI; the
legacy REPL does not auto-resume after the round trip (state carry-over
is read-only via ``_replay_transcript_to_host``). See
``my-docs/ch13-terminal-ui-refactoring-plan.md`` working assumption A12.
"""

from __future__ import annotations

from pathlib import Path


def build_repl_banner() -> str:
    """One-line banner used by tests to confirm the module loads."""

    return (
        "ClawCodex REPL (legacy prompt_toolkit + rich is default; "
        "Textual TUI opt-in via --tui or /tui)."
    )


def launch_repl(
    *,
    prefer_tui: bool | None = None,
    workspace_root: Path | None = None,
    stream: bool = True,
) -> int:
    """Boot the interactive UI.

    Args:
        prefer_tui: ``True`` forces the Textual TUI, ``False`` forces the
            legacy Rich REPL, ``None`` auto-detects via
            :func:`src.entrypoints.tui.should_use_tui`.
        workspace_root: Override the workspace root for the Textual path.
        stream: Whether the legacy REPL should enable live streaming.

    Returns:
        A conventional process exit code.
    """

    from src.entrypoints.tui import should_use_tui

    if should_use_tui(prefer_tui):
        from src.entrypoints.tui import TUIOptions, run_tui

        return run_tui(TUIOptions(workspace_root=workspace_root, stream=stream))

    from src.cli import start_repl

    return start_repl(stream=stream)

"""REPL launcher shims.

Mirrors the role of ``typescript/src/replLauncher.tsx`` in the Python
port: a thin factory that decides how to boot the interactive REPL.
As of the default-UI parity milestone the Textual TUI is the default
and the legacy Rich / prompt_toolkit REPL is an opt-in fallback.

This module intentionally stays slim: the actual wiring lives in
:mod:`src.entrypoints.tui` (Textual path) and :mod:`src.repl.core`
(legacy path). Keeping a dedicated entrypoint here makes it easy for
embedders (e.g. future ``replLauncher`` callers in the `demos/` tree)
to pick a UI without reaching into the CLI module.
"""

from __future__ import annotations

from pathlib import Path


def build_repl_banner() -> str:
    """One-line banner used by tests to confirm the module loads."""

    return "Claw Codex REPL (Textual default; use --legacy-repl for Rich)."


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

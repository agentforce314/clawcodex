"""Downstream TUI entrypoint."""

from __future__ import annotations

from clawcodex_ext.tui.app import ClawCodexExtTUI
from src.entrypoints.tui import TUIOptions, _run_tui_with_app


def run_tui(options: TUIOptions) -> int:
    """Boot the downstream-owned Textual TUI."""

    return _run_tui_with_app(options, app_cls=ClawCodexExtTUI)


__all__ = ["TUIOptions", "run_tui"]

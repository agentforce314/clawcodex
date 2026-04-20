"""Textual-based interactive TUI for Claw Codex.

Phase 11 of the CLI/REPL/terminal-UI refactor. This package mirrors the
architectural shape of ``typescript/src/screens/REPL.tsx`` — a retained-mode
component tree with a header, scrollable transcript, status bar and prompt
input — using `Textual <https://textual.textualize.io>`_, the only mainstream
Python TUI framework with the same layout/state model as Ink/React.

Public entry point: :func:`src.entrypoints.tui.run_tui`. This package deliberately
contains *no* import of ``src.repl.core`` (the legacy Rich REPL) so the two
interactive stacks can evolve independently.
"""

from .app import ClawCodexTUI
from .messages import (
    AgentRunFinished,
    AgentRunStarted,
    AssistantChunk,
    AssistantMessage,
    ToolEventMessage,
)

__all__ = [
    "ClawCodexTUI",
    "AgentRunFinished",
    "AgentRunStarted",
    "AssistantChunk",
    "AssistantMessage",
    "ToolEventMessage",
]

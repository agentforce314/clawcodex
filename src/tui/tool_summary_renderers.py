"""Ch5/F.4 — display-rendering helpers, separated from the loop.

The legacy ``src/tool_system/agent_loop.py`` module bundled two
concerns: the synchronous agent loop (``run_agent_loop``) and the
UI-rendering helpers (``summarize_tool_use``, ``summarize_tool_result``,
``ToolEvent``, the callback typedefs). Phase F migrated the production
loop to ``query()`` + the F.1 adapter, leaving ``run_agent_loop`` as a
backstop for non-migrated paths. This module is the canonical home for
the display helpers; ``agent_loop.py`` re-exports them for backward
compatibility but new callers should import here.

Why a TUI module? The summarize_* helpers are TUI/UI concerns —
they shape a tool's name + input/output into a short string for
transcript rendering. The loop itself doesn't need them.
"""
from __future__ import annotations

# Re-export the renderers + types from the legacy module. The body
# still lives in agent_loop.py until F.4's final cleanup; this module
# is the import path callers should use today.
from src.tool_system.agent_loop import (
    AgentLoopResult,
    TextChunkHandler,
    ToolEvent,
    ToolEventHandler,
    summarize_tool_result,
    summarize_tool_use,
)

__all__ = [
    "AgentLoopResult",
    "TextChunkHandler",
    "ToolEvent",
    "ToolEventHandler",
    "summarize_tool_result",
    "summarize_tool_use",
]

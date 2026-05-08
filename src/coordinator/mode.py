"""Coordinator-mode gates and tool-set filters — Chunk G / WI-8.1-8.3.

Mirrors ``typescript/src/coordinator/coordinatorMode.ts`` (excluding
the ~370-line system prompt body which lives in
``src/coordinator/prompt.py`` for SRP).

* ``is_coordinator_mode`` — env-var gate over ``CLAUDE_CODE_COORDINATOR_MODE``.
* ``match_session_mode`` — sync resumed-session mode with env var.
* ``INTERNAL_WORKER_TOOLS`` — frozenset of tool names workers cannot
  use (TeamCreate / TeamDelete / SendMessage / StructuredOutput).
* ``filter_coordinator_tools`` — produce the coordinator's restricted
  tool list (``Agent`` / ``SendMessage`` / ``TaskStop`` only).
* ``filter_worker_tools`` — produce a worker's tool list (everything
  the parent has, minus ``INTERNAL_WORKER_TOOLS``).
* ``get_coordinator_user_context`` — produce the
  ``workerToolsContext`` user-message block the chapter §"Worker
  Context" describes.
"""
from __future__ import annotations

import logging
import os
from typing import Final, Iterable, Literal, TYPE_CHECKING

from src.utils.env import is_env_truthy

if TYPE_CHECKING:
    from src.tool_system.build_tool import Tool

logger = logging.getLogger(__name__)


# Env-var gate. Mirrors ``coordinatorMode.ts:36-41``.
_COORDINATOR_MODE_ENV: Final[str] = "CLAUDE_CODE_COORDINATOR_MODE"


def is_coordinator_mode() -> bool:
    """Return True iff the active session is in coordinator mode.

    The env var is the runtime signal; ``match_session_mode`` flips
    it on resume so the resumed session's stored mode wins. Without
    this flow, a coordinator session resumed as a regular agent
    would lose awareness of its workers (and vice versa).
    """
    return is_env_truthy(_COORDINATOR_MODE_ENV)


SessionMode = Literal["coordinator", "normal"]


def match_session_mode(session_mode: SessionMode | None) -> str | None:
    """Sync the env var with a resumed session's stored mode.

    Mirrors ``coordinatorMode.ts:49-78``. Returns a banner string when
    a flip happened (the caller surfaces it to the user) or ``None``
    when no flip was needed.

    Behavior:
    * ``session_mode is None`` (sessions stored before mode tracking
      existed) → no-op, return None.
    * ``session_mode == current_is_coordinator`` → no-op, return None.
    * Otherwise: flip the env var so ``is_coordinator_mode()`` returns
      the right value for the resumed session, return a banner.
    """
    if session_mode is None:
        return None

    current_is_coordinator = is_coordinator_mode()
    session_is_coordinator = session_mode == "coordinator"

    if current_is_coordinator == session_is_coordinator:
        return None

    if session_is_coordinator:
        os.environ[_COORDINATOR_MODE_ENV] = "1"
    else:
        # ``del`` rather than setting to "" so future reads return
        # absent, not falsy-stringy.
        os.environ.pop(_COORDINATOR_MODE_ENV, None)

    return (
        "Entered coordinator mode to match resumed session."
        if session_is_coordinator
        else "Exited coordinator mode to match resumed session."
    )


# ---------------------------------------------------------------------------
# Tool-set filters — coordinator gets 3 tools; workers lose 4
# ---------------------------------------------------------------------------

# Tools workers cannot use. ``"StructuredOutput"`` is the literal
# string that TS's ``SYNTHETIC_OUTPUT_TOOL_NAME`` resolves to (per
# ``SyntheticOutputTool.ts:20``); the apparent mismatch with the TS
# constant name is intentional — pin the bytes the model sees.
INTERNAL_WORKER_TOOLS: Final[frozenset[str]] = frozenset({
    "TeamCreate",
    "TeamDelete",
    "SendMessage",
    "StructuredOutput",
})

# The coordinator gets EXACTLY these three tools. The chapter calls
# this out as core: "the coordinator's power comes not from having
# more tools, but from having fewer." No Read, no Edit, no Bash.
_COORDINATOR_ALLOWED_TOOLS: Final[frozenset[str]] = frozenset({
    "Agent",
    "SendMessage",
    "TaskStop",
})


def filter_coordinator_tools(all_tools: Iterable["Tool"]) -> list["Tool"]:
    """Return only the three tools the coordinator may use."""
    return [t for t in all_tools if t.name in _COORDINATOR_ALLOWED_TOOLS]


def filter_worker_tools(all_tools: Iterable["Tool"]) -> list["Tool"]:
    """Return everything except ``INTERNAL_WORKER_TOOLS``.

    Workers receive standard tools (Read / Edit / Bash / etc.) plus
    any MCP tools the parent has registered; only the swarm-internal
    coordination tools are excluded.
    """
    return [t for t in all_tools if t.name not in INTERNAL_WORKER_TOOLS]


# ---------------------------------------------------------------------------
# Worker-tools context block — surfaced to the coordinator's prompt
# ---------------------------------------------------------------------------


def get_coordinator_user_context(
    mcp_clients: Iterable[object] | None = None,
    *,
    scratchpad_dir: str | None = None,
) -> dict[str, str]:
    """Build the ``workerToolsContext`` user-message section for
    coordinator mode.

    Mirrors ``coordinatorMode.ts:80-109``. Returns an empty dict when
    not in coordinator mode (the section is gated on mode, not on
    tools-list shape — non-coordinator agents shouldn't see it). When
    coordinator mode is active, returns ``{"workerToolsContext": "..."}``
    with the three tool names + optional MCP server names + optional
    scratchpad note.

    The scratchpad block is gated by the ``tengu_scratch`` feature
    flag in TS; for chapter-10 we surface the note only when
    ``scratchpad_dir`` is supplied (the actual scratchpad creation
    is out-of-scope per plan §3 — wire-up only).
    """
    if not is_coordinator_mode():
        return {}

    # Pre-compute the worker tools list. We can't import the full
    # registry here (would circular), so we list the canonical names
    # the chapter mentions and let the actual tool filter at the
    # registry level decide what the worker actually has.
    worker_tools = (
        "Read, Bash, Edit, Glob, Grep, Write, WebSearch, WebFetch, "
        "TodoWrite, Skill, EnterWorktree, ExitWorktree"
    )

    parts = [f"Workers spawned via Agent have access to these tools: {worker_tools}"]

    mcp_server_names = sorted(
        {getattr(c, "name", "") for c in (mcp_clients or [])} - {""}
    )
    if mcp_server_names:
        parts.append(
            "Workers also have access to MCP tools from connected MCP "
            f"servers: {', '.join(mcp_server_names)}"
        )

    if scratchpad_dir:
        parts.append(
            f"Scratchpad directory: {scratchpad_dir}\n"
            "Workers can read and write here without permission "
            "prompts. Use this for durable cross-worker knowledge — "
            "structure files however fits the work."
        )

    return {"workerToolsContext": "\n\n".join(parts)}


__all__ = [
    "is_coordinator_mode",
    "match_session_mode",
    "INTERNAL_WORKER_TOOLS",
    "filter_coordinator_tools",
    "filter_worker_tools",
    "get_coordinator_user_context",
]

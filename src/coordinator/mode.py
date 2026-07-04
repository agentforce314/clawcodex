"""Coordinator-mode gates and tool-set filters — Chunk G / WI-8.1-8.3.

Mirrors ``typescript/src/coordinator/coordinatorMode.ts`` (excluding
the ~370-line system prompt body which lives in
``src/coordinator/prompt.py`` for SRP).

* ``is_coordinator_mode`` — env-var gate over ``CLAUDE_CODE_COORDINATOR_MODE``.
* ``match_session_mode`` — sync resumed-session mode with env var.
* ``INTERNAL_WORKER_TOOLS`` — frozenset of tool names workers cannot
  use (TeamCreate / TeamDelete / SendMessage / StructuredOutput).
* ``filter_coordinator_tools`` — produce the coordinator's restricted
  tool list (``Agent`` / ``SendMessage`` / ``TaskStop`` /
  ``StructuredOutput`` + PR-activity subscription MCP tools; mirrors
  ``COORDINATOR_MODE_ALLOWED_TOOLS`` + ``applyCoordinatorToolFilter``).
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

from src.agent.constants import ASYNC_AGENT_ALLOWED_TOOLS
from src.utils.env import is_env_truthy

if TYPE_CHECKING:
    from src.tool_system.build_tool import Tool
    from src.tool_system.registry import ToolRegistry

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
# Tool-set filters — coordinator keeps 4 tools (+ PR-activity MCP); workers
# lose the swarm-internal 4
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

# The coordinator's allowed tool set. Mirrors TS
# ``COORDINATOR_MODE_ALLOWED_TOOLS`` (``constants/tools.ts:105-110``):
# Agent + TaskStop + SendMessage + SyntheticOutput — whose model-facing
# name is the literal ``"StructuredOutput"`` (``SyntheticOutputTool.ts:20``),
# needed so structured-output-constrained headless runs still work in
# coordinator mode. The chapter's "exactly three tools" phrasing predates
# the TS snapshot's fourth entry. No Read, no Edit, no Bash — "the
# coordinator's power comes not from having more tools, but from having
# fewer."
_COORDINATOR_ALLOWED_TOOLS: Final[frozenset[str]] = frozenset({
    "Agent",
    "SendMessage",
    "TaskStop",
    "StructuredOutput",
})

# MCP tool-name suffixes for GitHub PR-activity subscription. These are
# lightweight orchestration actions the coordinator calls directly rather
# than delegating to workers; matched by suffix since the MCP server-name
# prefix may vary. Mirrors ``utils/toolPool.ts:11-18``.
PR_ACTIVITY_TOOL_SUFFIXES: Final[tuple[str, ...]] = (
    "subscribe_pr_activity",
    "unsubscribe_pr_activity",
)


def is_pr_activity_subscription_tool(name: str) -> bool:
    """True for MCP PR-activity subscription tools (suffix match).

    Mirrors ``isPrActivitySubscriptionTool`` (``utils/toolPool.ts:16-18``).
    """
    return any(name.endswith(suffix) for suffix in PR_ACTIVITY_TOOL_SUFFIXES)


def filter_coordinator_tools(all_tools: Iterable["Tool"]) -> list["Tool"]:
    """Filter a tool pool to the set allowed in coordinator mode.

    Mirrors ``applyCoordinatorToolFilter`` (``utils/toolPool.ts:35-41``):
    the four allowed tools plus PR-activity subscription MCP tools, which
    are always allowed since subscription management is orchestration.
    """
    return [
        t for t in all_tools
        if t.name in _COORDINATOR_ALLOWED_TOOLS
        or is_pr_activity_subscription_tool(t.name)
    ]


def coordinator_main_loop_registry(registry: "ToolRegistry") -> "ToolRegistry":
    """Main-loop view of ``registry`` under the coordinator gate.

    Identity when not in coordinator mode. In coordinator mode, returns a
    NEW registry holding only the coordinator-allowed tools (+ PR-activity
    MCP tools) — the port of applying ``applyCoordinatorToolFilter`` on the
    main-loop tool assembly (``toolPool.ts:35-41`` interactive,
    ``main.tsx:1871-1879`` headless).

    Non-mutating by design: ``make_agent_tool`` captures the FULL registry
    (``tool_system/defaults.py:18-24``) and subagent spawns list tools from
    that captured object (``tool_system/tools/agent.py``), so workers keep
    their full pool — the same separation TS documents at
    ``AgentTool.tsx:568-575`` ("Workers always get their tools from
    assembleToolPool … so they aren't affected by the parent's tool
    restrictions"). Tool objects are shared between the two registries.

    Call this FRESH at each main-loop consumption point (it is cheap):
    building from ``list_tools()`` bakes in the disabled-MCP-server
    exclusion at compute time, and a fresh view also picks up late MCP
    registration and live MCP tool refresh.
    """
    if not is_coordinator_mode():
        return registry
    from src.tool_system.registry import ToolRegistry

    return ToolRegistry(filter_coordinator_tools(registry.list_tools()))


def filter_worker_tools(all_tools: Iterable["Tool"]) -> list["Tool"]:
    """Return everything except ``INTERNAL_WORKER_TOOLS``.

    Workers receive standard tools (Read / Edit / Bash / etc.) plus
    any MCP tools the parent has registered; only the swarm-internal
    coordination tools are excluded.

    NB (chapter-derived, deliberately UNWIRED): TS has no runtime filter
    at this seam — ``INTERNAL_WORKER_TOOLS`` shapes only the *rendered
    string* in the coordinator's user context, while actual worker tool
    filtering is the async-agent whitelist
    (``src/agent/agent_tool_utils.py:filter_tools_for_agent``, mirror of
    ``agentToolUtils.ts``). Do not wire this as a runtime filter; that
    would diverge from TS.
    """
    return [t for t in all_tools if t.name not in INTERNAL_WORKER_TOOLS]


# ---------------------------------------------------------------------------
# Worker-tools context block — surfaced to the coordinator's prompt
# ---------------------------------------------------------------------------


# Tools surfaced in SIMPLE mode. Mirrors the literal list at
# ``coordinatorMode.ts:88-91`` (``[BASH_TOOL_NAME, FILE_READ_TOOL_NAME,
# FILE_EDIT_TOOL_NAME]``). Kept as a module-level tuple so the
# round-2 ch10 sort-and-render path has one source of truth.
_SIMPLE_MODE_WORKER_TOOLS: Final[tuple[str, ...]] = ("Bash", "Read", "Edit")


def _build_worker_tools_string() -> str:
    """Build the comma-separated worker tools list for the coordinator
    user context. Mirrors ``coordinatorMode.ts:88-95``.

    SIMPLE branch (``CLAUDE_CODE_SIMPLE`` truthy): the literal three
    tools ``[Bash, Read, Edit]``, sorted alphabetically. This matches
    the SIMPLE worker-capabilities sentence in the coordinator system
    prompt (``prompt.py:78-81``) so the coordinator's prompt and
    user-context agree on what workers can do.

    Default branch: ``ASYNC_AGENT_ALLOWED_TOOLS - INTERNAL_WORKER_TOOLS``
    sorted alphabetically. Reading from ``ASYNC_AGENT_ALLOWED_TOOLS``
    rather than hardcoding the list ties the rendered context to the
    same source of truth as the actual runtime tool filter — adding
    or removing a tool from the async-allowed set automatically
    flows through to the coordinator's user context.
    """
    if is_env_truthy("CLAUDE_CODE_SIMPLE"):
        return ", ".join(sorted(_SIMPLE_MODE_WORKER_TOOLS))
    eligible = ASYNC_AGENT_ALLOWED_TOOLS - INTERNAL_WORKER_TOOLS
    return ", ".join(sorted(eligible))


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
    with the appropriate tool names + optional MCP server names +
    optional scratchpad note.

    The worker tools list now branches on ``CLAUDE_CODE_SIMPLE`` to
    match TS exactly (round-2 fix); see ``_build_worker_tools_string``.

    The scratchpad block is gated by the ``tengu_scratch`` Statsig
    feature flag in TS; Python has no Statsig infra yet (backlog
    item #5 in ``ch10-phase11-backlog.md``), so for now we surface
    the note unconditionally when ``scratchpad_dir`` is supplied.
    The actual scratchpad creation is out-of-scope per plan §3.
    """
    if not is_coordinator_mode():
        return {}

    worker_tools = _build_worker_tools_string()

    # Byte-parity with ``coordinatorMode.ts:97`` (``via the ${AGENT_TOOL_NAME}
    # tool``) — the model-facing tool reference must name the tool exactly.
    parts = [
        f"Workers spawned via the Agent tool have access to these tools: {worker_tools}"
    ]

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
    "PR_ACTIVITY_TOOL_SUFFIXES",
    "is_pr_activity_subscription_tool",
    "coordinator_main_loop_registry",
    "filter_coordinator_tools",
    "filter_worker_tools",
    "get_coordinator_user_context",
]

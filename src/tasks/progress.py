"""Progress tracking for running agents — Chunk C / WI-2.4.

Mirrors the ``ProgressTracker`` machinery in
``typescript/src/tasks/LocalAgentTask/LocalAgentTask.tsx:23-115``. Two
chapter-correctness pillars hold the design together:

1. **Token aggregation arithmetic.** ``input_tokens`` (and the cache
   read/creation cousins) come back from the API cumulatively per call —
   a turn-15 response includes every prior turn's input. Sum them and
   you double-count by turn count squared. ``output_tokens`` is per-turn
   — sum them. Pre-WI-2.4 ``finalize_agent_tool`` short-circuited this
   with ``total_tokens=0`` (gap analysis §2.2 row); the chapter-10 spec
   is "keep latest input + sum outputs."
2. **``recent_activities`` cap-5.** The chapter calls this out explicitly:
   bounded ring buffer, not unbounded list. Tracks "what was the agent
   just doing" for the UI panel, nothing more — the full history lives
   on disk in the JSONL transcript (Chunk C / WI-2.2).

The module also lands the ``AgentProgress`` snapshot dataclass that
``LocalAgentTaskState.progress`` carries (post-WI-2.3 expansion).

Refactor-in-place note (per critic): ``count_tool_uses`` in
``src.agent.agent_tool_utils`` is unchanged — it still counts post-hoc.
The new ``ProgressTracker`` is the *live* counter that
``_collect_agent_messages`` feeds during the run; ``finalize_agent_tool``
reads the tracker's accumulated totals at the end instead of computing
``total_tokens=0``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.types.messages import AssistantMessage


# Cap for the recent-activities ring buffer. Chapter §"Progress Tracking".
MAX_RECENT_ACTIVITIES: int = 5

# Tools whose tool_use entries should NOT appear in the recent-activities
# preview. Mirrors TS LocalAgentTask.tsx:79-80 (omits StructuredOutput
# / SyntheticOutput from preview because it's an internal tool).
_PREVIEW_BLACKLIST: frozenset[str] = frozenset({
    "StructuredOutput",
    "SyntheticOutput",
})


@dataclass
class ToolActivity:
    """One tool-use event recorded for the recent-activities preview.

    Mirrors ``LocalAgentTask.tsx:23-32``. ``activity_description`` is
    pre-computed at recording time (TS uses ``Tool.getActivityDescription``;
    Python plumbs the same idea via ``ActivityDescriptionResolver``) so
    the UI doesn't have to introspect the input dict on every render.
    """

    tool_name: str
    input: dict[str, Any] = field(default_factory=dict)
    activity_description: str | None = None
    is_search: bool | None = None
    is_read: bool | None = None


@dataclass
class AgentProgress:
    """Snapshot of an agent's progress, suitable for storing on
    ``LocalAgentTaskState.progress`` and emitting to UI consumers.

    The five-field shape matches ``AgentProgress`` in
    ``LocalAgentTask.tsx:33-39``.
    """

    tool_use_count: int = 0
    token_count: int = 0
    last_activity: ToolActivity | None = None
    recent_activities: list[ToolActivity] = field(default_factory=list)
    summary: str | None = None


@dataclass
class ProgressTracker:
    """Mutable accumulator that watches assistant messages stream by.

    Token semantics (chapter §"Progress Tracking", line for line):
    * ``latest_input_tokens`` — kept as the latest value because the API
      returns input cumulatively per call. Includes ``cache_creation_input_tokens``
      and ``cache_read_input_tokens`` (TS does the same: Anthropic's
      response shape splits them out and the chapter sums them into the
      "latest" value).
    * ``cumulative_output_tokens`` — summed because output is per-turn.

    ``recent_activities`` is bounded at ``MAX_RECENT_ACTIVITIES``;
    ``update_progress_from_message`` drops the oldest entry on overflow.
    """

    tool_use_count: int = 0
    latest_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    recent_activities: list[ToolActivity] = field(default_factory=list)


def total_tokens_from_tracker(tracker: ProgressTracker) -> int:
    """Combined input + output total, suitable for ``AgentProgress.token_count``
    and the post-hoc ``finalize_agent_tool`` aggregation.

    ``latest_input_tokens`` already covers the cumulative-per-call API
    semantics; ``cumulative_output_tokens`` is the running sum of the
    per-turn output values. Adding them yields the chapter-correct
    "tokens used so far" number.
    """
    return tracker.latest_input_tokens + tracker.cumulative_output_tokens


# Resolver hook — given a tool name + input, return a human-readable
# activity description for the preview ("Reading src/foo.ts", etc.).
# Returning ``None`` is fine; the UI falls back to the tool name.
ActivityDescriptionResolver = Callable[[str, dict[str, Any]], "str | None"]


def update_progress_from_message(
    tracker: ProgressTracker,
    message: "AssistantMessage",
    *,
    resolve_activity_description: ActivityDescriptionResolver | None = None,
) -> None:
    """Fold one assistant message into the tracker.

    Mirrors ``updateProgressFromMessage`` in ``LocalAgentTask.tsx:68-96``.
    Updates token counts (per the cumulative-vs-per-turn split), pushes
    one ``ToolActivity`` per non-blacklisted ``tool_use`` block, and
    drops the oldest activity on cap-5 overflow.

    Tolerant of missing/partial usage payloads — ``message.usage`` is
    ``dict[str, Any] | None`` per ``src.types.messages``, and a stub
    test message that omits it is a no-op for token accounting.
    """
    # Local imports defer the cycle: ``progress`` doesn't need to know
    # about message types at module load.
    from src.types.content_blocks import ToolUseBlock
    from src.types.messages import AssistantMessage

    if not isinstance(message, AssistantMessage):
        return

    usage = message.usage if isinstance(message.usage, dict) else None
    if usage is not None:
        # Cumulative-per-call inputs: include cache read/creation token
        # counts so the total matches what the API billed for.
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        tracker.latest_input_tokens = input_tokens + cache_creation + cache_read

        tracker.cumulative_output_tokens += int(usage.get("output_tokens", 0) or 0)

    content = message.content
    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, ToolUseBlock):
            continue
        tracker.tool_use_count += 1
        if block.name in _PREVIEW_BLACKLIST:
            # Counted in tool_use_count (chapter does the same) but
            # omitted from the preview ring.
            continue
        block_input = getattr(block, "input", None)
        if not isinstance(block_input, dict):
            block_input = {}
        description: str | None = None
        if resolve_activity_description is not None:
            try:
                description = resolve_activity_description(block.name, block_input)
            except Exception:
                # A resolver bug must not poison the tracker.
                description = None
        tracker.recent_activities.append(
            ToolActivity(
                tool_name=block.name,
                input=block_input,
                activity_description=description,
            )
        )
        # Drop oldest entries on overflow.
        while len(tracker.recent_activities) > MAX_RECENT_ACTIVITIES:
            tracker.recent_activities.pop(0)


def get_progress_update(tracker: ProgressTracker) -> AgentProgress:
    """Project a ``ProgressTracker`` into an immutable ``AgentProgress``
    snapshot — what UI consumers store on ``LocalAgentTaskState.progress``.

    Returns a fresh ``AgentProgress`` per call; callers don't share the
    backing list with the tracker.
    """
    last = tracker.recent_activities[-1] if tracker.recent_activities else None
    return AgentProgress(
        tool_use_count=tracker.tool_use_count,
        token_count=total_tokens_from_tracker(tracker),
        last_activity=last,
        recent_activities=list(tracker.recent_activities),
    )


__all__ = [
    "MAX_RECENT_ACTIVITIES",
    "ToolActivity",
    "AgentProgress",
    "ProgressTracker",
    "ActivityDescriptionResolver",
    "update_progress_from_message",
    "get_progress_update",
    "total_tokens_from_tracker",
]

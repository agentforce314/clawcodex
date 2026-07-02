"""Server-side delivery helpers for background-task completion notifications.

Background workflows (and background agents) run on daemon threads. When one
finishes it pushes a ``<task-notification>`` envelope onto the process-global
queue in :mod:`src.utils.message_queue_manager` (see
``src.tasks.local_workflow.enqueue_workflow_notification`` and
``src.tasks.local_agent``'s agent equivalent). The interactive consumer of that
queue used to be the Rich REPL (``src/repl/task_notifications.py``, removed in
#566); this module is its agent-server successor — the worker loop drains the
queue between turns and, for every drained envelope:

* emits a deterministic completion **banner** to the client (a
  ``system/task_notification`` frame the TUI renders as a persistent transcript
  line), and
* hands the envelopes back to the agent as one **turn** so it can read the
  result and summarize it conversationally — the Claude Code "the research is
  done…" behavior.

Everything here is pure and side-effect free (no emit, no registry mutation,
no queue access) so it unit-tests without a live server; the glue in
``src/server/agent_server.py`` is the thin part that wires it to the wire
protocol and the worker inbox. Banners are plain text (the client styles its
own transcript lines), unlike the Rich-markup originals.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.constants.xml import (
    OUTPUT_FILE_TAG,
    STATUS_TAG,
    SUMMARY_TAG,
    TASK_ID_TAG,
)
from src.workflow.progress import format_duration, format_tokens

# Status → (icon, past-tense verb) for the banner head.
_BANNER_STYLE: dict[str, tuple[str, str]] = {
    "completed": ("✔", "completed"),
    "failed": ("✗", "failed"),
    "killed": ("⊘", "stopped"),
}


def _field(xml: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", xml, re.DOTALL)
    return m.group(1).strip() if m else None


def parse_task_id(xml: str) -> Optional[str]:
    """Pull the ``<task-id>`` out of a notification envelope (for registry
    correlation), or ``None`` if absent."""
    return _field(xml, TASK_ID_TAG)


def _elapsed_seconds(state: Any) -> Optional[float]:
    start = getattr(state, "start_time", None)
    end = getattr(state, "end_time", None)
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return end - start
    return None


def _stat_bits(state: Any) -> list[str]:
    progress = getattr(state, "progress", None)
    bits: list[str] = []
    try:
        phases = list(getattr(progress, "phases", None) or [])
        total = sum(len(p.agents) for p in phases)
        if total:
            bits.append(f"{total} agents")
    except Exception:
        pass
    try:
        tok = int(getattr(progress, "token_total", 0) or 0)
        if tok:
            bits.append(f"{format_tokens(tok)} tok")
    except Exception:
        pass
    dur = format_duration(_elapsed_seconds(state))
    if dur:
        bits.append(dur)
    return bits


def _task_label(state: Any) -> str:
    """Display name for a finished task: the workflow name for workflow runs,
    the task description for background agents (which share the queue), then a
    generic fallback."""
    for attr in ("workflow_name", "description"):
        val = getattr(state, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "task"


def format_completion_banner(state: Any) -> list[str]:
    """Deterministic completion banner (plain-text lines) from a terminal
    ``local_workflow`` / ``local_agent`` task state.

    Never raises — every field degrades to a sensible default, because this
    runs on the agent-server worker loop where an exception would be
    disruptive.
    """
    status = (getattr(state, "status", "") or "").lower()
    icon, verb = _BANNER_STYLE.get(status, ("•", status or "finished"))
    name = _task_label(state)

    tail = (" · " + " · ".join(bits)) if (bits := _stat_bits(state)) else ""
    lines = [f"{icon} {name} {verb}{tail}"]

    if status == "failed":
        err = getattr(state, "error", None)
        if err:
            lines.append(f"  {err}")
    out = getattr(state, "output_file", None)
    if out:
        lines.append(f"  run journal → {out}")
    return lines


def format_completion_banner_xml(xml: str) -> list[str]:
    """Fallback banner built straight from an envelope, for the rare case where
    the task state has already been evicted by delivery time."""
    status = (_field(xml, STATUS_TAG) or "").lower()
    icon, _verb = _BANNER_STYLE.get(status, ("•", status or "finished"))
    summary = _field(xml, SUMMARY_TAG) or "Background task finished"
    lines = [f"{icon} {summary}"]
    out = _field(xml, OUTPUT_FILE_TAG)
    if out:
        lines.append(f"  run journal → {out}")
    return lines


def render_banner(xml: str, state: Any | None) -> list[str]:
    """Prefer the rich registry-state banner; fall back to the envelope."""
    if state is not None:
        try:
            return format_completion_banner(state)
        except Exception:
            pass
    return format_completion_banner_xml(xml)


_TURN_PREAMBLE = (
    "<system-reminder>\n"
    "One or more background tasks you launched have finished. Their results are "
    "delivered below as <task-notification> envelopes — these are system events, "
    "not a new request the user typed. For each finished task: briefly confirm "
    "it's done, summarize the key findings or output for the user, and tell them "
    "where the full result is saved (the <output-file>, or any file path named "
    "inside the <result>). If the <result> points at a file you must read to "
    "report it accurately (for example a CSV), read it first. Keep it concise.\n"
    "</system-reminder>"
)


def build_notification_turn(notifications: list[str]) -> str:
    """Assemble drained ``<task-notification>`` envelopes into a single agent
    turn: a guiding preamble followed by the raw envelopes."""
    body = "\n\n".join(n.strip() for n in notifications if n and n.strip())
    return f"{_TURN_PREAMBLE}\n\n{body}"


__all__ = [
    "parse_task_id",
    "format_completion_banner",
    "format_completion_banner_xml",
    "render_banner",
    "build_notification_turn",
]

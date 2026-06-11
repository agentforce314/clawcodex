"""REPL-side delivery of background-task completion notifications.

Background workflows (and background agents) run on daemon threads. When one
finishes it pushes a ``<task-notification>`` envelope onto the process-global
queue in :mod:`src.utils.message_queue_manager` (see
``src.tasks.local_workflow.enqueue_workflow_notification``). The chapter
deferred the *consumer* side of that queue ("WI-3.3"); for the interactive REPL
this module is it.

The REPL drains the queue at each turn boundary and, for every drained envelope:

* prints a deterministic completion **banner** (so the user learns a run
  finished the moment it does, with a pointer to where the output lives), and
* hands the envelope back to the agent as a **turn** so it can read the result
  and summarize it conversationally — the Claude Code "the research is done…"
  behavior.

Everything here is pure and side-effect free (no console, no registry mutation,
no queue access) so it unit-tests without a live REPL; the REPL glue in
``src/repl/core.py`` is the thin part that wires it to the console and ``chat``.
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

# Status → (icon, rich color, past-tense verb) for the banner head.
_BANNER_STYLE: dict[str, tuple[str, str, str]] = {
    "completed": ("✔", "green", "completed"),
    "failed": ("✗", "red", "failed"),
    "killed": ("⊘", "yellow", "stopped"),
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


def format_completion_banner(state: Any) -> list[str]:
    """Deterministic completion banner (rich-markup lines) from a terminal
    ``local_workflow`` task state.

    Never raises — every field degrades to a sensible default, because this runs
    on the REPL's main loop where an exception would be disruptive.
    """
    status = (getattr(state, "status", "") or "").lower()
    icon, color, verb = _BANNER_STYLE.get(status, ("•", "cyan", status or "finished"))
    name = getattr(state, "workflow_name", None) or "workflow"

    tail = (" · " + " · ".join(bits)) if (bits := _stat_bits(state)) else ""
    lines = [f"[{color}]{icon}[/{color}] [bold]{name}[/bold] {verb}{tail}"]

    if status == "failed":
        err = getattr(state, "error", None)
        if err:
            lines.append(f"  [red]{err}[/red]")
    out = getattr(state, "output_file", None)
    if out:
        lines.append(f"  [dim]run journal → {out}[/dim]")
    return lines


def format_completion_banner_xml(xml: str) -> list[str]:
    """Fallback banner built straight from an envelope, for the rare case where
    the task state has already been evicted by delivery time."""
    status = (_field(xml, STATUS_TAG) or "").lower()
    icon, color, _verb = _BANNER_STYLE.get(status, ("•", "cyan", status or "finished"))
    summary = _field(xml, SUMMARY_TAG) or "Background task finished"
    lines = [f"[{color}]{icon}[/{color}] {summary}"]
    out = _field(xml, OUTPUT_FILE_TAG)
    if out:
        lines.append(f"  [dim]run journal → {out}[/dim]")
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

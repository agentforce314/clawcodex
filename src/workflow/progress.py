"""Progress model for a running workflow.

This is the in-memory tree the ``LocalWorkflowTask`` / ``/workflows`` view will
render: ordered **phases**, each holding the **agents** that ran under it
(label, status, tokens), plus narrator **log** lines. Every ``phase()``,
``log()``, and ``agent()`` lifecycle transition updates it and fires the
optional ``on_change`` callback so the TUI can repaint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

AgentStatus = Literal["running", "completed", "failed", "skipped", "cached"]

#: Status glyphs for the /workflows monitor (mirrors the Claude Code view).
_STATUS_ICON = {
    "running": "●",
    "completed": "✔",
    "failed": "✗",
    "skipped": "⊘",
    "cached": "⚡",
}


@dataclass
class AgentRecord:
    index: int
    label: str
    phase: Optional[str]
    status: AgentStatus = "running"
    tokens: int = 0
    error: Optional[str] = None
    #: The agent's deterministic call-path key (e.g. "0.1") — lets the UI/task
    #: layer target this specific agent for skip/retry.
    key: str = ""
    #: Display metadata surfaced in the /workflows monitor.
    agent_type: str = ""
    tool_count: int = 0
    started_at: Optional[float] = None  # time.monotonic() at start (display only)
    elapsed: Optional[float] = None     # seconds, set on finish

    @property
    def icon(self) -> str:
        return _STATUS_ICON.get(self.status, "•")


@dataclass
class PhaseRecord:
    title: str
    detail: Optional[str] = None
    agents: list[AgentRecord] = field(default_factory=list)

    @property
    def token_total(self) -> int:
        return sum(a.tokens for a in self.agents)

    @property
    def done_count(self) -> int:
        """Agents that have finished (any non-running status)."""
        return sum(1 for a in self.agents if a.status != "running")


class WorkflowProgress:
    def __init__(
        self,
        phases_meta: Optional[list[dict]] = None,
        *,
        on_change: Callable[["WorkflowProgress"], None] | None = None,
    ) -> None:
        # Seed declared phases (from meta.phases) so the tree shows them before
        # any agent runs; phase() started later by title reuses the same record.
        self._phases: list[PhaseRecord] = [
            PhaseRecord(title=p.get("title", ""), detail=p.get("detail"))
            for p in (phases_meta or [])
            if isinstance(p, dict) and p.get("title")
        ]
        self._logs: list[str] = []
        self._current_phase: Optional[str] = None
        self._on_change = on_change

    # ── mutations ──────────────────────────────────────────────────────────
    def start_phase(self, title: str) -> None:
        self._current_phase = title
        if not any(p.title == title for p in self._phases):
            self._phases.append(PhaseRecord(title=title))
        self._changed()

    def log(self, message: str) -> None:
        self._logs.append(message)
        self._changed()

    def agent_started(
        self,
        index: int,
        label: str,
        phase: Optional[str],
        key: str = "",
        agent_type: str = "",
    ) -> AgentRecord:
        phase = phase or self._current_phase
        record = AgentRecord(
            index=index, label=label, phase=phase, key=key,
            agent_type=agent_type, started_at=time.monotonic(),
        )
        self._phase_for(phase).agents.append(record)
        self._changed()
        return record

    def agent_finished(
        self,
        record: AgentRecord,
        *,
        status: AgentStatus,
        tokens: int = 0,
        error: Optional[str] = None,
        tool_count: int = 0,
    ) -> None:
        record.status = status
        record.tokens = tokens
        record.error = error
        record.tool_count = tool_count
        if record.started_at is not None:
            record.elapsed = time.monotonic() - record.started_at
        self._changed()

    # ── reads ──────────────────────────────────────────────────────────────
    @property
    def phases(self) -> list[PhaseRecord]:
        return self._phases

    @property
    def logs(self) -> list[str]:
        return list(self._logs)

    @property
    def current_phase(self) -> Optional[str]:
        return self._current_phase

    @property
    def agent_count(self) -> int:
        return sum(len(p.agents) for p in self._phases)

    @property
    def token_total(self) -> int:
        return sum(p.token_total for p in self._phases)

    def summary(self) -> str:
        phase = self._current_phase or (self._phases[-1].title if self._phases else "starting")
        return f"{phase} · {self.agent_count} agents · {self.token_total} tokens"

    # ── internals ──────────────────────────────────────────────────────────
    def _phase_for(self, phase: Optional[str]) -> PhaseRecord:
        title = phase or "main"
        for record in self._phases:
            if record.title == title:
                return record
        record = PhaseRecord(title=title)
        self._phases.append(record)
        return record

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change(self)


# ---------------------------------------------------------------------------
# Rendering for the /workflows monitor (shared by the REPL command + TUI dialog)
# ---------------------------------------------------------------------------

def format_tokens(n: int) -> str:
    """Compact token count: 79600 -> '79.6k', 950 -> '950'."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def format_duration(seconds: Optional[float]) -> str:
    """Compact duration: 75 -> '1m 15s', 45 -> '45s', None -> ''."""
    if seconds is None:
        return ""
    s = int(seconds)
    if s >= 60:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s}s"


def format_agent_line(agent: AgentRecord, *, indent: str = "      ") -> str:
    """One rich agent row: '✔ label  type   12.3k tok · 5 tools · 1m 15s'."""
    parts = [f"{format_tokens(agent.tokens)} tok"]
    if agent.tool_count:
        parts.append(f"{agent.tool_count} tools")
    dur = format_duration(agent.elapsed)
    if dur:
        parts.append(dur)
    typ = f"  {agent.agent_type}" if agent.agent_type else ""
    line = f"{indent}{agent.icon} {agent.label}{typ}   {' · '.join(parts)}"
    if agent.error:
        line += f"  — {agent.error[:60]}"
    return line


def render_run_lines(state: object) -> list[str]:
    """Render a workflow run's header + phases -> agents tree as display lines.

    Mirrors the Claude Code /workflows monitor: per-phase progress (done/total)
    and per-agent status icon, type, tokens, tool count, and duration. ``state``
    is duck-typed (a LocalWorkflowTask or any object exposing ``workflow_name``,
    ``status``, and ``progress``) so the REPL command and the TUI dialog share it.
    """
    name = getattr(state, "workflow_name", None) or "workflow"
    status = getattr(state, "status", "?") or "?"
    progress = getattr(state, "progress", None)
    phases = list(getattr(progress, "phases", None) or [])

    total = sum(len(p.agents) for p in phases)
    done = sum(p.done_count for p in phases)
    header = f"{name}  [{status}]"
    if total:
        tok = getattr(progress, "token_total", 0)
        header += f"  ·  {done}/{total} agents · {format_tokens(tok)} tok"
    lines = [header]

    if not phases:
        lines.append("  (no phases yet)")
        return lines

    for p in phases:
        prog = f"{p.done_count}/{len(p.agents)}" if p.agents else "—"
        lines.append(f"  ▸ {p.title}  ({prog} · {format_tokens(p.token_total)} tok)")
        for a in p.agents:
            lines.append(format_agent_line(a))
    return lines

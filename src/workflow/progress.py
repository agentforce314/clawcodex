"""Progress model for a running workflow.

This is the in-memory tree the ``LocalWorkflowTask`` / ``/workflows`` view will
render: ordered **phases**, each holding the **agents** that ran under it
(label, status, tokens), plus narrator **log** lines. Every ``phase()``,
``log()``, and ``agent()`` lifecycle transition updates it and fires the
optional ``on_change`` callback so the TUI can repaint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

AgentStatus = Literal["running", "completed", "failed", "skipped", "cached"]


@dataclass
class AgentRecord:
    index: int
    label: str
    phase: Optional[str]
    status: AgentStatus = "running"
    tokens: int = 0
    error: Optional[str] = None


@dataclass
class PhaseRecord:
    title: str
    detail: Optional[str] = None
    agents: list[AgentRecord] = field(default_factory=list)

    @property
    def token_total(self) -> int:
        return sum(a.tokens for a in self.agents)


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

    def agent_started(self, index: int, label: str, phase: Optional[str]) -> AgentRecord:
        phase = phase or self._current_phase
        record = AgentRecord(index=index, label=label, phase=phase)
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
    ) -> None:
        record.status = status
        record.tokens = tokens
        record.error = error
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

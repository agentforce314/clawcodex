"""Legacy porting-workbench types.

Originally defined in the (long-deleted) top-level ``src/models.py``,
then parked in ``src/models/__init__.py`` "for backward compatibility
with src/commands.py etc." — modules that ch01 round-2 (P3) relocated
into this package. ch01 round-3 completes the move: these types are
audit-only scaffolding and have no production consumers.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Subsystem:
    name: str
    path: str
    file_count: int
    notes: str


@dataclass(frozen=True)
class PortingModule:
    name: str
    responsibility: str
    source_hint: str
    status: str = "planned"


@dataclass(frozen=True)
class PermissionDenial:
    tool_name: str
    reason: str


@dataclass(frozen=True)
class UsageSummary:
    input_tokens: int = 0
    output_tokens: int = 0

    def add_turn(self, prompt: str, output: str) -> "UsageSummary":
        return UsageSummary(
            input_tokens=self.input_tokens + len(prompt.split()),
            output_tokens=self.output_tokens + len(output.split()),
        )


@dataclass
class PortingBacklog:
    title: str
    modules: list[PortingModule] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        return [
            f"- {m.name} [{m.status}] — {m.responsibility} (from {m.source_hint})"
            for m in self.modules
        ]

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostTracker:
    total_units: int = 0
    events: list[str] = field(default_factory=list)

    def record(self, label: str, units: int) -> None:
        self.total_units += units
        self.events.append(f'{label}:{units}')

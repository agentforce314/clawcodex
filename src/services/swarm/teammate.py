"""Teammate spawning and lifecycle management.

Mirrors TypeScript swarm/teammate.ts — manages the lifecycle of teammate
processes including creation, monitoring, and termination.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)


class TeammateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TeammateConfig:
    """Configuration for spawning a teammate."""
    prompt: str
    model: str = "claude-sonnet-4-6"
    cwd: str = ""
    max_turns: int = 50
    allowed_tools: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Teammate:
    """A running or completed teammate instance."""
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    config: TeammateConfig = field(default_factory=lambda: TeammateConfig(prompt=""))
    status: TeammateStatus = TeammateStatus.PENDING
    result: str = ""
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    turn_count: int = 0

    @property
    def elapsed_seconds(self) -> float:
        end = self.completed_at or time.time()
        return end - self.started_at

    @property
    def is_active(self) -> bool:
        return self.status in (TeammateStatus.PENDING, TeammateStatus.RUNNING)


class TeammateManager:
    """Manages a pool of teammate instances."""

    def __init__(self, max_concurrent: int = 5) -> None:
        self._teammates: dict[str, Teammate] = {}
        self._max_concurrent = max_concurrent
        self._on_complete: list[Callable[[Teammate], None]] = []

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._teammates.values() if t.is_active)

    @property
    def all_teammates(self) -> list[Teammate]:
        return list(self._teammates.values())

    def spawn(self, config: TeammateConfig) -> Teammate:
        """Create and register a new teammate."""
        if self.active_count >= self._max_concurrent:
            raise RuntimeError(
                f"Max concurrent teammates ({self._max_concurrent}) reached"
            )
        teammate = Teammate(config=config, status=TeammateStatus.RUNNING)
        self._teammates[teammate.id] = teammate
        logger.info("Spawned teammate %s with prompt: %s", teammate.id, config.prompt[:50])
        return teammate

    def complete(self, teammate_id: str, result: str = "", error: str | None = None) -> None:
        """Mark a teammate as completed or failed."""
        teammate = self._teammates.get(teammate_id)
        if teammate is None:
            raise KeyError(f"Unknown teammate: {teammate_id}")
        teammate.completed_at = time.time()
        teammate.result = result
        teammate.error = error
        teammate.status = TeammateStatus.FAILED if error else TeammateStatus.COMPLETED
        for cb in self._on_complete:
            try:
                cb(teammate)
            except Exception:
                logger.exception("Error in on_complete callback")

    def cancel(self, teammate_id: str) -> None:
        """Cancel a running teammate."""
        teammate = self._teammates.get(teammate_id)
        if teammate and teammate.is_active:
            teammate.status = TeammateStatus.CANCELLED
            teammate.completed_at = time.time()

    def get(self, teammate_id: str) -> Teammate | None:
        return self._teammates.get(teammate_id)

    def on_complete(self, cb: Callable[[Teammate], None]) -> None:
        self._on_complete.append(cb)

    def cancel_all(self) -> int:
        """Cancel all active teammates. Returns count cancelled."""
        count = 0
        for t in self._teammates.values():
            if t.is_active:
                t.status = TeammateStatus.CANCELLED
                t.completed_at = time.time()
                count += 1
        return count

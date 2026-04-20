"""Swarm permission synchronization.

Mirrors TypeScript swarm/permissions.ts — synchronizes permission decisions
across teammates so one approval applies to all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PermissionDecision:
    """A cached permission decision."""
    tool_name: str
    rule_content: str | None
    allowed: bool
    timestamp: float = 0.0


class SwarmPermissionSync:
    """Synchronizes permission decisions across teammates.

    When a user approves/denies a tool use in one teammate, the decision
    propagates to all other teammates to avoid duplicate prompts.
    """

    def __init__(self) -> None:
        self._decisions: dict[str, PermissionDecision] = {}

    def record_decision(
        self,
        tool_name: str,
        rule_content: str | None,
        allowed: bool,
    ) -> None:
        """Record a permission decision."""
        import time
        key = self._make_key(tool_name, rule_content)
        self._decisions[key] = PermissionDecision(
            tool_name=tool_name,
            rule_content=rule_content,
            allowed=allowed,
            timestamp=time.time(),
        )

    def check_decision(
        self,
        tool_name: str,
        rule_content: str | None,
    ) -> bool | None:
        """Check if a decision exists. Returns True/False or None if unknown."""
        key = self._make_key(tool_name, rule_content)
        decision = self._decisions.get(key)
        if decision is None:
            return None
        return decision.allowed

    def clear(self) -> None:
        self._decisions.clear()

    @property
    def decision_count(self) -> int:
        return len(self._decisions)

    @staticmethod
    def _make_key(tool_name: str, rule_content: str | None) -> str:
        return f"{tool_name}:{rule_content or '*'}"

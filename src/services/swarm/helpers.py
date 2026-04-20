"""Swarm team helpers.

Mirrors TypeScript swarm/helpers.ts — utility functions for team coordination.
"""
from __future__ import annotations

from .teammate import Teammate, TeammateManager, TeammateStatus


def get_active_teammates(manager: TeammateManager) -> list[Teammate]:
    """Get all currently active teammates."""
    return [t for t in manager.all_teammates if t.is_active]


def format_team_summary(manager: TeammateManager) -> str:
    """Format a human-readable summary of the team status."""
    teammates = manager.all_teammates
    if not teammates:
        return "No teammates."

    lines = [f"Team: {len(teammates)} teammate(s)"]
    for t in teammates:
        status_icon = {
            TeammateStatus.RUNNING: "🔄",
            TeammateStatus.COMPLETED: "✅",
            TeammateStatus.FAILED: "❌",
            TeammateStatus.CANCELLED: "⛔",
            TeammateStatus.PENDING: "⏳",
        }.get(t.status, "?")
        prompt_preview = t.config.prompt[:40] + ("..." if len(t.config.prompt) > 40 else "")
        lines.append(f"  {status_icon} [{t.id}] {t.status.value}: {prompt_preview}")

    active = manager.active_count
    if active > 0:
        lines.append(f"  Active: {active}")

    return "\n".join(lines)

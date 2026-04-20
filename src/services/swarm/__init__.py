"""Swarm/Teammates subsystem.

Provides teammate spawning, permission synchronization, and team coordination.
Mirrors TypeScript swarm/ directory.
"""
from __future__ import annotations

from .teammate import Teammate, TeammateConfig, TeammateManager, TeammateStatus
from .permissions import SwarmPermissionSync
from .helpers import format_team_summary, get_active_teammates

__all__ = [
    "Teammate",
    "TeammateConfig",
    "TeammateManager",
    "TeammateStatus",
    "SwarmPermissionSync",
    "format_team_summary",
    "get_active_teammates",
]

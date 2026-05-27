"""Re-export the shared Issue model for convenient imports within the orchestrator extension."""

from __future__ import annotations

from ..issue import Issue

__all__ = ["Issue"]

"""Orchestrator CLI subcommands.

clawcodex orchestrator <subcommand>

All orchestrator operations (run, status, issues, pause, resume, stop, etc.)
live under this unified entry point.
"""

from __future__ import annotations

from .clarify import run as run_clarify
from .dashboard import run as run_dashboard
from .inject import run as run_inject
from .issues import run as run_issues
from .lifecycle import run as run_lifecycle
from .run import run as run_run
from .status import run as run_status
from .workspace import run as run_workspace

__all__ = [
    "run_clarify",
    "run_dashboard",
    "run_inject",
    "run_issues",
    "run_lifecycle",
    "run_run",
    "run_status",
    "run_workspace",
]
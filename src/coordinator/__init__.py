"""Coordinator subsystem — Chunk G / Phase 8.

Replaces the prior placeholder module body that exposed only the
parity-snapshot metadata. The chapter-10 coordinator pattern lives in:

* ``src/coordinator/mode.py`` — ``is_coordinator_mode`` / ``match_session_mode``
  / ``INTERNAL_WORKER_TOOLS`` / tool-set filters / ``get_coordinator_user_context``.
* ``src/coordinator/prompt.py`` — verbatim port of the ~370-line
  coordinator system prompt with two interpolation points
  (tool-name constants + ``worker_capabilities`` env-flag branch).
* ``src/coordinator/worker_agent.py`` — ``WORKER_AGENT`` definition
  spread from ``GENERAL_PURPOSE_AGENT`` with ``INTERNAL_WORKER_TOOLS``
  filtered out.

Parity-snapshot metadata still exposed (the parity audit reads it).
"""
from __future__ import annotations

import json
from pathlib import Path

from src.coordinator.mode import (
    INTERNAL_WORKER_TOOLS,
    filter_coordinator_tools,
    filter_worker_tools,
    get_coordinator_user_context,
    is_coordinator_mode,
    match_session_mode,
)
from src.coordinator.prompt import get_coordinator_system_prompt
from src.coordinator.worker_agent import WORKER_AGENT, get_coordinator_agents

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'reference_data' / 'subsystems' / 'coordinator.json'
_SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())

ARCHIVE_NAME = _SNAPSHOT['archive_name']
MODULE_COUNT = _SNAPSHOT['module_count']
SAMPLE_FILES = tuple(_SNAPSHOT['sample_files'])
PORTING_NOTE = (
    f"Python coordinator package for '{ARCHIVE_NAME}' "
    f"({MODULE_COUNT} archived module reference(s)). "
    f"Chapter-10 / Chunk G implements is_coordinator_mode + system prompt + "
    f"INTERNAL_WORKER_TOOLS filter + WORKER agent + fork mutex."
)

__all__ = [
    "ARCHIVE_NAME",
    "MODULE_COUNT",
    "PORTING_NOTE",
    "SAMPLE_FILES",
    "INTERNAL_WORKER_TOOLS",
    "WORKER_AGENT",
    "filter_coordinator_tools",
    "filter_worker_tools",
    "get_coordinator_agents",
    "get_coordinator_system_prompt",
    "get_coordinator_user_context",
    "is_coordinator_mode",
    "match_session_mode",
]

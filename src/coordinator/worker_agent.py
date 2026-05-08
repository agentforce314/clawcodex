"""WORKER agent definition — Chunk G / WI-8.5.

Mirrors ``typescript/src/coordinator/workerAgent.ts``. Workers in
coordinator mode are spread from ``GENERAL_PURPOSE_AGENT`` with two
edits:

* ``agent_type`` becomes ``"worker"`` so the coordinator system
  prompt's ``subagent_type: "worker"`` calls resolve correctly.
* ``when_to_use`` is rephrased for the coordinator-mode role.

Workers do NOT lose tools at the agent-definition level — the
``INTERNAL_WORKER_TOOLS`` filter (``coordinator/mode.py``) handles
that at tool-set construction time. Keeping the filter at the
mode-level (rather than baking it into the agent definition) means
the same WORKER definition can be reused for non-coordinator paths
in the future without re-editing.

``get_coordinator_agents()`` returns the canonical agent list for
coordinator-mode sessions (matches ``workerAgent.ts:16-18``).
"""
from __future__ import annotations

from dataclasses import replace

from src.agent.agent_definitions import (
    EXPLORE_AGENT,
    GENERAL_PURPOSE_AGENT,
    PLAN_AGENT,
    AgentDefinition,
)


# Spread GENERAL_PURPOSE → tweak agent_type + when_to_use. Mirrors
# ``workerAgent.ts:9-14``.
WORKER_AGENT: AgentDefinition = replace(
    GENERAL_PURPOSE_AGENT,
    agent_type="worker",
    when_to_use=(
        "Worker agent for coordinator mode. Executes tasks autonomously "
        "— research, implementation, or verification."
    ),
)


def get_coordinator_agents() -> list[AgentDefinition]:
    """Return the canonical agent list for coordinator-mode sessions.

    Mirrors ``workerAgent.ts:16-18``: ``[WORKER, GENERAL_PURPOSE,
    EXPLORE, PLAN]``. ``WORKER`` first so the coordinator system
    prompt's ``subagent_type: "worker"`` resolves to the right
    definition; ``GENERAL_PURPOSE`` / ``EXPLORE`` / ``PLAN`` follow
    so the legacy spawning paths still work in coordinator mode.
    """
    return [WORKER_AGENT, GENERAL_PURPOSE_AGENT, EXPLORE_AGENT, PLAN_AGENT]


__all__ = [
    "WORKER_AGENT",
    "get_coordinator_agents",
]

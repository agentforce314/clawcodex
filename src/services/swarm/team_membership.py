"""Team-membership predicates — Chunk F / WI-6.4.

Single source of truth for "is the active agent the team lead?" Used
by SendMessage's plan-approval gate (sender side, Chunk F / WI-7.3),
the mailbox poller's envelope verification (receiver side, per critic
concern C3), and Phase 9's permission-forwarding bridge.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tool_system.context import ToolContext


def is_team_lead(context: "ToolContext") -> bool:
    """True iff the active agent is the team lead.

    Mirrors the gate at ``typescript/src/tools/SendMessageTool/SendMessageTool.ts:442``
    and ``:487``. Compares ``context.agent_id`` against
    ``context.team["lead_agent_id"]``. Returns False (does not raise)
    when:

    * No team is active on the context.
    * No ``agent_id`` is set on the context.
    * The team file lacks a ``lead_agent_id``.

    **Callers MUST treat False as "not authorized," not "team
    unavailable"** — the predicate intentionally collapses both into
    False so authorization checks are straightforward (a not-team-lead
    branch handles both safely; granting permission would be
    catastrophic in either).
    """
    team = getattr(context, "team", None)
    agent_id = getattr(context, "agent_id", None)
    if team is None or not agent_id:
        return False
    if not isinstance(team, dict):
        return False
    lead_agent_id = team.get("lead_agent_id")
    return bool(lead_agent_id) and agent_id == lead_agent_id


__all__ = [
    "is_team_lead",
]

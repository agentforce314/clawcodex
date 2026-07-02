"""ch08 round-4 WI-1 — per-subagent model resolution.

Port of TS ``getAgentModel`` (``utils/model/agent.ts``, called at
``runAgent.ts:340``): resolve the model a subagent should run on from the
tool ``model`` param, the agent definition's ``model:`` frontmatter, and
the session model, with a ``CLAUDE_CODE_SUBAGENT_MODEL`` env override.

Multi-provider guard: the port runs on 7+ providers, and the abstract
aliases (``sonnet``/``opus``/``haiku``) only map on Anthropic-family
providers. So an alias/id the SESSION provider doesn't recognize falls
back to the session model rather than 400-ing the request.

Concurrency (ch07): Agent is now concurrency-safe, so N parallel
subagents share the session ``provider`` instance. This module only
COMPUTES the model string; the caller (``run_agent``) applies it to a
per-subagent provider CLONE and never mutates the shared provider.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_INHERIT = "inherit"
# Bare family aliases (TS agent.ts). A request for one of these that
# matches the parent's TIER keeps the parent's EXACT model rather than
# downgrading to the alias's canonical (older) target.
_FAMILY_ALIASES = ("opus", "sonnet", "haiku")


def _resolve_against_provider(
    value: str, session_provider: Any, *, trust_literal: bool = False,
) -> str:
    """Resolve an alias/id to the model the subagent should run on; inherit
    the session model on a miss. Never raises.

    - ``'inherit'``/empty → the session model.
    - A bare family alias whose tier == the parent's tier → the parent's
      EXACT model (critic M2 — TS ``aliasMatchesParentTier``; avoids the
      surprising same-tier downgrade, e.g. sonnet-4-6 → sonnet-4-2025...).
    - A full (non-alias) model id → trusted literally when ``trust_literal``
      (the env override / an explicit id — critic M3, TS
      ``parseUserSpecifiedModel``); otherwise gated by availability.
    - An alias mapped to a model the provider serves → that canonical id.
    - Anything the provider doesn't serve → the session model.
    """
    session_model = getattr(session_provider, "model", "") or ""
    normalized = (value or "").strip().lower()
    if not normalized or normalized == _INHERIT:
        return session_model

    # M2 — same-tier alias keeps the parent's exact model.
    if normalized in _FAMILY_ALIASES and normalized in session_model.lower():
        return session_model

    try:
        from src.models.model import canonical_model_name

        canonical = canonical_model_name(value)
    except Exception:  # noqa: BLE001 — resolution failure → inherit
        return session_model

    # M3 — a full id (canonical didn't change it, i.e. not a known alias)
    # from the env override or an explicit pin is trusted literally, so it
    # survives static-list staleness / proxy deployments with custom names.
    is_bare_alias = normalized in _FAMILY_ALIASES
    if trust_literal and not is_bare_alias:
        return value

    try:
        available = session_provider.get_available_models() or []
        available = [str(m) for m in available]
    except Exception:  # noqa: BLE001 — provider can't enumerate → inherit
        available = []
    if canonical in available:
        return canonical
    # Not served by this provider (e.g. 'haiku' on a DeepSeek session).
    # Inherit rather than 400. Elevated to WARNING (M3) so an ignored
    # explicit pin is observable, not silently dropped at debug.
    logger.warning(
        "agent model %r (→ %r) is not available on the session provider; "
        "inheriting the session model %r",
        value, canonical, session_model,
    )
    return session_model


def get_agent_model(
    tool_model: str | None,
    agent_def_model: str | None,
    session_provider: Any,
) -> str:
    """Resolve the subagent's model. Precedence (TS getAgentModel):
    ``CLAUDE_CODE_SUBAGENT_MODEL`` env > tool ``model`` param > agent-def
    ``model:`` > ``'inherit'`` (= the session model). Always returns a
    non-empty model when the session provider has one; never raises."""
    env_override = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")
    if env_override:
        # M3 — the env override is honored more literally (a full id it
        # names is trusted; TS agent.ts:43-45 bypasses provider gating).
        return _resolve_against_provider(
            env_override, session_provider, trust_literal=True,
        )

    chosen = tool_model or agent_def_model or _INHERIT
    # A tool param / frontmatter that names a full model id is trusted
    # literally; bare aliases still go through availability/tier logic.
    return _resolve_against_provider(
        chosen, session_provider, trust_literal=True,
    )

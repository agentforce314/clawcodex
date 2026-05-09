"""Register agent/skill frontmatter hooks as session-scoped.

Phase-3 / WI-3.3. Mirrors TS
``typescript/src/utils/hooks/registerFrontmatterHooks.ts:18-67``.

The general frontmatter-hook registration entry point. Used by anything
that loads frontmatter ``hooks:`` blocks (skills, agents, etc.). Critically:
**this is the home of the ``Stop → SubagentStop`` conversion**, gated on
``is_agent: bool`` per the gap-analysis B1 correction.

The conversion exists because subagents trigger ``SubagentStop`` (not the
top-level ``Stop`` event) when they finish. A ``Stop`` hook declared in an
agent's frontmatter would never fire without the rewrite — the gap analysis
gap #11 and the chapter §"Sub-agents" both call this out.

``register_skill_hooks.py`` does NOT call into this module — skills don't
get the conversion. The split is intentional: skills run in the parent
session, so their ``Stop`` hooks should fire on top-level ``Stop``; agent
frontmatter hooks run in a sub-session, so their ``Stop`` hooks need
``SubagentStop`` to actually fire.
"""

from __future__ import annotations

import logging
from typing import Any

from .hook_types import HookConfig, HookEvent, HookSource
from .session_hooks import SessionHookRegistry, add_session_hook, remove_session_hook

logger = logging.getLogger(__name__)


async def register_frontmatter_hooks(
    *,
    registry: SessionHookRegistry,
    session_id: str,
    frontmatter_hooks: dict[str, list[dict[str, Any]]] | None,
    source_name: str,
    is_agent: bool = False,
    skill_root: str | None = None,
) -> int:
    """Register frontmatter hooks; convert ``Stop → SubagentStop`` if
    ``is_agent`` is True.

    Returns the count registered.

    ``source_name`` is informational (e.g., ``"agent foo"`` /
    ``"skill /commit"``); used in debug logs to trace registrations back to
    their declarer.
    """
    if not frontmatter_hooks:
        return 0

    registered = 0
    for event_name, matcher_groups in frontmatter_hooks.items():
        if not isinstance(matcher_groups, list):
            continue

        # ``Stop → SubagentStop`` conversion (B1: lives here, not in
        # register_skill_hooks). Mirrors registerFrontmatterHooks.ts:37-45.
        target_event: HookEvent = event_name  # type: ignore[assignment]
        if is_agent and event_name == "Stop":
            target_event = "SubagentStop"
            logger.debug(
                "register_frontmatter_hooks: Stop → SubagentStop for %s "
                "(is_agent=True)",
                source_name,
            )

        for matcher_group in matcher_groups:
            if not isinstance(matcher_group, dict):
                continue
            matcher = matcher_group.get("matcher", "") or ""
            inner_hooks = matcher_group.get("hooks", [])
            if not isinstance(inner_hooks, list):
                continue
            for raw in inner_hooks:
                if not isinstance(raw, dict):
                    continue
                config = HookConfig(
                    type=raw.get("type", "command"),
                    command=raw.get("command", ""),
                    timeout=raw.get("timeout"),
                    matcher=matcher or raw.get("matcher"),
                    url=raw.get("url"),
                    prompt_text=raw.get("promptText") or raw.get("prompt_text"),
                    agent_instructions=raw.get("agentInstructions") or raw.get("agent_instructions"),
                    if_condition=raw.get("if_condition") or raw.get("if"),
                    once=bool(raw.get("once", False)),
                    skill_root=skill_root,
                    source=HookSource.SESSION_HOOK,
                )

                on_success = _make_once_remover(
                    registry=registry,
                    session_id=session_id,
                    event=target_event,
                    hook_config=config,
                ) if config.once else None

                await add_session_hook(
                    registry=registry,
                    session_id=session_id,
                    event=target_event,
                    matcher=matcher,
                    hook=config,
                    on_success=on_success,
                    skill_root=skill_root,
                )
                registered += 1

    if registered > 0:
        logger.debug(
            "Registered %d frontmatter hook(s) from %s (session=%s, is_agent=%s)",
            registered, source_name, session_id, is_agent,
        )
    return registered


def _make_once_remover(
    *,
    registry: SessionHookRegistry,
    session_id: str,
    event: HookEvent,
    hook_config: HookConfig,
):
    """See register_skill_hooks._make_once_remover — same shape, separate
    instance so each registrar's logging is distinct.
    """
    import asyncio as _asyncio

    def _on_success() -> None:
        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "once: true removal skipped — no running loop "
                "(session=%s, event=%s, command=%r)",
                session_id, event, hook_config.command,
            )
            return
        loop.create_task(
            remove_session_hook(
                registry=registry,
                session_id=session_id,
                event=event,
                hook=hook_config,
            )
        )

    return _on_success

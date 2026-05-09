"""Register skill-frontmatter hooks as session-scoped.

Phase-3 / WI-3.2. Mirrors TS ``typescript/src/utils/hooks/registerSkillHooks.ts``.

When a skill with frontmatter ``hooks:`` is invoked, each declared hook
becomes a session-scoped entry that fires for the lifetime of the session.
Hooks with ``once: true`` are auto-removed after their first successful
firing.

**Critical: this module does NOT do the ``Stop → SubagentStop`` conversion.**
That conversion lives in ``register_frontmatter_hooks.py`` (Phase-3 / WI-3.3),
gated on ``is_agent: bool``. ``registerSkillHooks.ts:20-64`` forwards each
event verbatim to ``addSessionHook``; the conversion is exclusively a
``registerFrontmatterHooks`` concern (registerFrontmatterHooks.ts:37-45).

This split was a critic correction during the gap analysis (B1) — earlier
plan revisions had attributed the conversion to ``registerSkillHooks``,
which was wrong.

Skill frontmatter shape (from ``skills/loader.py``):
    hooks:
      PreToolUse:
        - matcher: "Bash"
          hooks:
            - type: command
              command: ".claude/scripts/audit.sh"
              once: true
"""

from __future__ import annotations

import logging
from typing import Any

from .hook_types import HookConfig, HookEvent, HookSource
from .session_hooks import SessionHookRegistry, add_session_hook, remove_session_hook

logger = logging.getLogger(__name__)


async def register_skill_hooks(
    *,
    registry: SessionHookRegistry,
    session_id: str,
    skill_hooks: dict[str, list[dict[str, Any]]] | None,
    skill_name: str,
    skill_root: str | None = None,
) -> int:
    """Register a skill's frontmatter hooks as session-scoped entries.

    Returns the number of hooks registered.

    ``skill_hooks`` is the parsed frontmatter ``hooks:`` field, shaped like:
        { event: [ { matcher, hooks: [HookConfig-shaped dicts] } ] }

    Hooks tagged with ``HookSource.SESSION_HOOK`` so the registry's priority
    sort places them after policy/user/project/local but before plugin hooks
    (PLUGIN_HOOK has the 999 sentinel, sorts last). Skill-declared
    ``skill_root`` propagates to ``HookConfig.skill_root`` so WI-1.5's
    ``CLAUDE_PLUGIN_ROOT`` injection populates correctly when the hook fires.

    Each ``once: true`` hook is wired with an ``on_success`` callback that
    schedules removal via ``asyncio.create_task`` — fire-and-forget, so the
    executor's main loop doesn't await the lock acquisition (per N2 +
    A10's sync-mutator contract).
    """
    if not skill_hooks:
        return 0

    registered = 0
    for event_name, matcher_groups in skill_hooks.items():
        if not isinstance(matcher_groups, list):
            continue
        event: HookEvent = event_name  # type: ignore[assignment]
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
                    event=event,
                    hook_config=config,
                ) if config.once else None

                await add_session_hook(
                    registry=registry,
                    session_id=session_id,
                    event=event,  # NO Stop→SubagentStop conversion (B1).
                    matcher=matcher,
                    hook=config,
                    on_success=on_success,
                    skill_root=skill_root,
                )
                registered += 1

    if registered > 0:
        logger.debug(
            "Registered %d session hook(s) from skill %r (session=%s)",
            registered, skill_name, session_id,
        )
    return registered


def _make_once_remover(
    *,
    registry: SessionHookRegistry,
    session_id: str,
    event: HookEvent,
    hook_config: HookConfig,
):
    """Build an on-success callback that fire-and-forgets removal of a
    ``once: true`` hook after it executes successfully.

    The callback is sync (the executor calls it synchronously after a hook
    fires) but it schedules an async removal task — preserves A10's
    sync-mutator-on-the-callsite contract while letting the actual lock
    acquisition happen on the loop.
    """
    import asyncio as _asyncio

    def _on_success() -> None:
        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — caller isn't on asyncio. Removal can't be
            # scheduled; log and let the next iteration of the loop
            # handle cleanup. In practice this branch is unreachable
            # (the executor is always inside ``_run_hooks_for_event``,
            # which is async).
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

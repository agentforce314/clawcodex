"""Session-scoped hook registration API.

Phase-3 / WI-3.1 (A9 rename: this is the *new* ``session_hooks.py``; the
pre-Phase-3 file with the same name has been moved to
``lifecycle_routers.py``).

Mirrors TS ``typescript/src/utils/hooks/sessionHooks.ts``. Session-scoped
hooks live in memory only — they're registered programmatically (e.g., by
``register_skill_hooks`` when a skill with frontmatter ``hooks:`` is
invoked) and disappear when the session ends. The chapter's
``HookSource.SESSION_HOOK`` source.

**Concurrency contract (assumption A10).**
The registry mutator is *synchronous* — never ``await`` inside it. The lock
itself is ``asyncio.Lock`` (per critic N2) because all callers run on the
asyncio loop; ``threading.RLock`` would block the event loop on contention.
Mutators that violate the sync contract get an ``asyncio`` deadlock between
the lock-holder and the bash-worker bridge in ``registry.py:_invoke_tool_call``.

**``once: true`` removal.**
A hook with ``once=True`` is removed after its first successful firing
(exit code 0, no blocking_error, no permission deny). The executor schedules
the removal via ``asyncio.create_task(remove_session_hook(...))`` — fire-and-
forget, so the executor's main loop doesn't await the lock acquisition.
Removal under concurrent firing is race-free because the registry's lock
guards the lookup-and-remove sequence.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from .hook_types import HookConfig, HookEvent

logger = logging.getLogger(__name__)


@dataclass
class SessionHookEntry:
    """A single session-scoped hook.

    The matcher is duplicated from ``config.matcher`` for fast
    lookup; the on-success callback is opaque to the registry (only the
    executor invokes it after a hook fires successfully).

    ``source_session_id`` is informational — useful for logs/diagnostics
    when a leaked hook needs to be traced back to the session that
    registered it.
    """
    config: HookConfig
    event: HookEvent
    matcher: str = ""
    on_success: Callable[[], None] | None = None
    source_session_id: str = ""


class SessionHookRegistry:
    """In-memory registry of session-scoped hooks, keyed by session_id.

    Per A10's contract: the lock is ``asyncio.Lock`` (not ``threading.RLock``)
    because every caller is on the asyncio loop. Mutator bodies must be
    synchronous — the registry's ``add``/``remove``/``clear`` await *only*
    on the lock acquisition itself; the body is plain dict mutation.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._hooks: dict[str, list[SessionHookEntry]] = {}

    async def add(self, session_id: str, entry: SessionHookEntry) -> None:
        async with self._lock:
            self._hooks.setdefault(session_id, []).append(entry)

    async def remove(
        self,
        session_id: str,
        event: HookEvent,
        hook_config: HookConfig,
    ) -> bool:
        """Remove the *first* entry matching ``(session_id, event, config)``.

        Returns True if an entry was removed. Identity is by (event, command,
        matcher) tuple — sufficient for ``once: true`` since each registered
        hook has a distinct combination in practice (the same skill can't
        register two byte-identical hooks for the same event).

        Race-free under concurrent firing: the lock guards the find-and-
        remove sequence, so two firings of the same ``once: true`` hook
        either find it (one wins) or don't (the other lost the race).
        """
        async with self._lock:
            entries = self._hooks.get(session_id, [])
            for i, entry in enumerate(entries):
                if (
                    entry.event == event
                    and entry.config.command == hook_config.command
                    and entry.config.matcher == hook_config.matcher
                    and entry.config.type == hook_config.type
                ):
                    del entries[i]
                    return True
            return False

    async def get_for_event(
        self,
        session_id: str,
        event: HookEvent,
    ) -> list[SessionHookEntry]:
        """Return a snapshot of entries for ``(session_id, event)``.

        Returns a fresh list — callers may iterate safely while other tasks
        mutate the registry.
        """
        async with self._lock:
            return [
                entry for entry in self._hooks.get(session_id, [])
                if entry.event == event
            ]

    async def clear(self, session_id: str) -> int:
        """Remove all hooks for ``session_id``. Returns count removed."""
        async with self._lock:
            entries = self._hooks.pop(session_id, [])
            return len(entries)

    async def count(self, session_id: str) -> int:
        async with self._lock:
            return len(self._hooks.get(session_id, []))


# ---------------------------------------------------------------------------
# Module-level helpers — mirror TS ``sessionHooks.ts`` exports.
# ---------------------------------------------------------------------------


async def add_session_hook(
    *,
    registry: SessionHookRegistry,
    session_id: str,
    event: HookEvent,
    matcher: str,
    hook: HookConfig,
    on_success: Callable[[], None] | None = None,
    skill_root: str | None = None,
) -> None:
    """Register a session-scoped hook.

    ``skill_root`` is optional metadata: when set, the hook's
    ``CLAUDE_PLUGIN_ROOT`` env var (WI-1.5) is populated to this path. We
    write through to ``HookConfig.skill_root`` rather than carry a separate
    field on ``SessionHookEntry`` so the executor's existing
    ``_build_hook_env`` (which reads ``hook.skill_root``) needs no special
    case for session-scoped hooks.
    """
    if skill_root is not None and not hook.skill_root:
        hook.skill_root = skill_root
    entry = SessionHookEntry(
        config=hook,
        event=event,
        matcher=matcher,
        on_success=on_success,
        source_session_id=session_id,
    )
    await registry.add(session_id, entry)


async def remove_session_hook(
    *,
    registry: SessionHookRegistry,
    session_id: str,
    event: HookEvent,
    hook: HookConfig,
) -> bool:
    return await registry.remove(session_id, event, hook)


async def get_session_hooks(
    *,
    registry: SessionHookRegistry,
    session_id: str,
    event: HookEvent,
) -> list[SessionHookEntry]:
    return await registry.get_for_event(session_id, event)


async def clear_session_hooks(
    *,
    registry: SessionHookRegistry,
    session_id: str,
) -> int:
    return await registry.clear(session_id)

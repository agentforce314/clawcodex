"""Process-singleton holder for the frozen ``HookConfigSnapshot``.

Plan reference: ``my-docs/ch02-bootstrap-refactoring-plan.md`` P2.2.

The chapter §"Phase 3: Setup" final paragraph defines the security
model: the hook configuration is "read from disk once, frozen into an
immutable snapshot, and used for the rest of the session. Later
modifications to the hooks configuration file on disk are ignored.
This prevents an attacker from modifying hook rules after the session
starts — the frozen snapshot is the only source of truth for
permission decisions."

This module holds that snapshot as a process singleton. The
``capture_hooks_config_snapshot()`` function is called once from
``src/setup.py:run_production_setup`` during plan phase 2; subsequent
hook consumers read via ``get_active_hook_config_manager()``.

The existing ``src/hooks/config_manager.py:HookConfigManager.load()``
remains the authoritative loader; this module is just the process-
singleton wiring that ensures load() is called exactly once at startup.
``reload_if_changed`` is reserved for explicit user commands (e.g., a
future ``/hooks`` slash command), never auto-triggered.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

__all__ = [
    "capture_hooks_config_snapshot",
    "get_active_hook_config_manager",
    "reset_hook_snapshot_for_test_only",
]


_logger = logging.getLogger("clawcodex.hooks.snapshot")

_lock = threading.Lock()
_manager: Any = None  # HookConfigManager | None — typed Any to dodge import cycle


def capture_hooks_config_snapshot() -> Any:
    """Load and freeze the hook config snapshot. Idempotent.

    Mirrors TS ``captureHooksConfigSnapshot`` at
    ``typescript/src/setup.ts:166``. First call loads from disk and
    stashes the manager; subsequent calls return the same instance.

    Synchronous wrapper around the async ``HookConfigManager.load()``.
    Safe to call from sync code (cli.main → run_production_setup) because
    no event loop is running yet. If a future caller invokes from
    inside an async context, it should use the async path directly
    (``await HookConfigManager(registry).load()``) — calling this from
    inside a running loop will raise ``RuntimeError`` via ``asyncio.run``.
    """
    global _manager
    with _lock:
        if _manager is not None:
            return _manager

        # Lazy imports avoid pulling the entire hooks subsystem into
        # every importer of this module (the trust-gate test only
        # needs the read API).
        from src.hooks.config_manager import HookConfigManager
        from src.hooks.registry import AsyncHookRegistry

        registry = AsyncHookRegistry()
        manager = HookConfigManager(registry=registry)
        try:
            asyncio.run(manager.load())
        except Exception as exc:  # noqa: BLE001 — best-effort load
            # Mirrors TS behavior: failures during the snapshot don't
            # crash startup. The snapshot is just empty.
            _logger.warning(
                "hook snapshot load failed: %s; continuing with empty snapshot",
                exc,
            )

        _manager = manager
        return manager


def get_active_hook_config_manager() -> Any:
    """Return the captured ``HookConfigManager`` or ``None``.

    Consumers (``ToolContext`` factory, ``hook_executor``) call this
    to read the frozen snapshot. Returns ``None`` if
    ``capture_hooks_config_snapshot()`` hasn't run yet — callers should
    treat this as "no hooks configured" (the snapshot model already
    handles missing/empty hooks gracefully).
    """
    return _manager


def reset_hook_snapshot_for_test_only() -> None:
    """Wipe the captured snapshot. Test-only.

    Gated by ``PYTEST_CURRENT_TEST`` — production callers cannot
    accidentally reset the snapshot mid-session. Matches the
    discipline used by ``bootstrap.state.reset_state_for_tests``.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") is None:
        raise RuntimeError(
            "reset_hook_snapshot_for_test_only can only be called in tests"
        )
    global _manager
    with _lock:
        _manager = None

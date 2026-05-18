"""Reactive UI state — the Python analogue of ``state/AppStateStore.ts``.

Phase 2.1 of the ch03 state refactor: a minimal, immutable-update-style
``AppState`` dataclass backed by ``src.utils.store.create_store``, with
a centralized ``on_change_app_state`` handler that bridges UI state
transitions to bootstrap-state mirrors and external persistence.

**Two-tier separation.** This module imports from
``src.bootstrap.state`` (for the mirror writes performed in
``on_change_app_state``) but never the reverse — the dependency direction
is preserved. ``src.bootstrap.state`` remains a DAG leaf per the
import-linter contract.

**Scope.** Phase 2.1 covers the *minimum* AppState fields that map to the
chapter's named side effects:

* ``main_loop_model`` → mirror to ``set_main_loop_model_override`` in
  bootstrap; persist to settings (placeholder until settings.json
  layering is wired).
* ``verbose`` → persist to global config.
* ``expanded_view`` → persist as legacy ``showExpandedTodos`` /
  ``showSpinnerTree`` (matches TS at ``onChangeAppState.ts:123-136``).
* ``permission_mode`` → notify external listeners (CCR/SDK status
  stream). Today the notifiers are no-ops; they become real once
  the CCR bridge is wired.

Many more AppState fields exist in TS (~86 total per the gap analysis).
They land in this dataclass when their consumers do. The chapter's
single-file discipline applies here too: do not split ``AppState`` into
per-domain dataclasses, even when the line count grows.

**Side-effect coverage contract.** ``_FIELD_HANDLERS`` is a registry
mapping each AppState field to a handler function. Fields with no
side effect get an explicit handler whose body is just ``return`` —
the function name (``_on_<field>_change``) is greppable and the
no-op decision is visible at the site. The unit test at
``tests/test_app_state.py::TestSideEffectCoverage`` asserts that every
field in the dataclass appears in the registry, so adding a new field
without a handler is a compile-time-equivalent failure rather than a
silent miss. This is the structural-coverage mechanism the chapter's
lesson demands.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field, fields as dc_fields
from typing import Any, Callable

from src.bootstrap.state import (
    set_main_loop_model_override,
)
from src.utils.store import Store, create_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AppState dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppState:
    """Frozen dataclass: the store treats each setState as
    ``(prev) -> new_instance`` where ``new_instance`` is built via
    ``dataclasses.replace(prev, ...)``.

    Use ``replace_state(prev, field=value)`` to update — the store's
    identity-skip check (``if next is prev``) requires a different
    reference on any meaningful change.
    """

    # Model selection (TS: state/AppStateStore.ts:93)
    main_loop_model: str | None = None

    # Verbose mode (TS: AppStateStore.ts:92)
    verbose: bool = False

    # Expanded view: 'none' | 'tasks' | 'teammates' (TS: AppStateStore.ts:96)
    expanded_view: str = "none"

    # Permission mode (slice of toolPermissionContext — TS: AppStateStore.ts:110).
    # Stored as a string here to avoid a tight coupling with the existing
    # permissions package's enums; the bridge handlers can normalize.
    permission_mode: str = "default"

    # ``initial_message`` — set by entrypoints to queue a prompt for the REPL
    # to process at startup. TS: AppStateStore.ts:406.
    initial_message: str | None = None

    # Advisor model (TS: AppStateStore.ts advisorModel). None = no /advisor.
    # Writes here fire ``_on_advisor_model_change``, which persists into
    # ``settings.advisor_model`` (the read channel — see
    # ``src/utils/advisor.py`` and ``src/query/query.py``) and invalidates
    # the settings cache so the next API call picks up the new value.
    advisor_model: str | None = None


def replace_state(state: AppState, **changes: Any) -> AppState:
    """Return a copy of ``state`` with ``changes`` applied. Equivalent
    to ``dataclasses.replace(state, ...)`` — exists as a named helper
    to keep the call sites readable."""
    return dataclasses.replace(state, **changes)


def get_default_app_state() -> AppState:
    """Mirror of TS ``getDefaultAppState`` (``AppStateStore.ts:458``)."""
    return AppState()


# ---------------------------------------------------------------------------
# Side-effect handlers (the on_change_app_state router)
# ---------------------------------------------------------------------------


def _on_main_loop_model_change(old: AppState, new: AppState) -> None:
    """Mirror model choice into bootstrap singleton.

    Matches TS at ``onChangeAppState.ts:97-120`` — when the user changes
    the model (via /model slash command or the model picker), the
    bootstrap-state override must update so the next API call reads the
    new value, and settings persistence happens as a side effect.

    Settings persistence is currently no-op — the settings.json layering
    lives in ``src.settings`` and the writer is not yet wired through
    a single chokepoint. Plan §P2.1 left this stub here as the wiring
    target.
    """
    if old.main_loop_model == new.main_loop_model:
        return
    set_main_loop_model_override(new.main_loop_model)
    logger.debug(
        "AppState.main_loop_model %s -> %s — mirrored to bootstrap",
        old.main_loop_model,
        new.main_loop_model,
    )
    # TODO: persist to user settings via ``src.settings`` once the
    # writer-side has a single chokepoint.


def _on_verbose_change(old: AppState, new: AppState) -> None:
    if old.verbose == new.verbose:
        return
    logger.debug("AppState.verbose %s -> %s", old.verbose, new.verbose)
    # TODO: persist to global config.


def _on_expanded_view_change(old: AppState, new: AppState) -> None:
    if old.expanded_view == new.expanded_view:
        return
    logger.debug(
        "AppState.expanded_view %s -> %s — persist as showExpandedTodos/showSpinnerTree",
        old.expanded_view,
        new.expanded_view,
    )
    # TODO: persist to global config as showExpandedTodos +
    # showSpinnerTree (TS: onChangeAppState.ts:123-136).


# Permission-mode notification hooks. Real listeners (CCR bridge, SDK
# status stream) are registered via ``set_permission_mode_listener`` and
# ``set_session_metadata_listener`` — see TS ``utils/sessionState.ts``.
# Today the Python equivalents are placeholder; the slots are here so
# adding real listeners later is a one-line wiring change.

_permission_mode_listener: Callable[[str], None] | None = None
_session_metadata_listener: Callable[[dict[str, Any]], None] | None = None


def set_permission_mode_listener(cb: Callable[[str], None] | None) -> None:
    """Register a callback for permission-mode changes. Mirrors TS
    ``setPermissionModeChangedListener`` (``utils/sessionState.ts:79``)."""
    global _permission_mode_listener
    _permission_mode_listener = cb


def set_session_metadata_listener(cb: Callable[[dict[str, Any]], None] | None) -> None:
    """Register a callback for external-metadata changes. Mirrors TS
    ``setSessionMetadataChangedListener`` (``utils/sessionState.ts:66``)."""
    global _session_metadata_listener
    _session_metadata_listener = cb


def _on_permission_mode_change(old: AppState, new: AppState) -> None:
    """Centralized side effect for permission-mode changes.

    Mirrors TS ``onChangeAppState.ts:67-94``: notify CCR external metadata
    AND the SDK status stream. The single chokepoint here is the *whole
    point* of the architecture — pre-Chapter-3, this notification was
    duplicated across 6+ mutation sites and was broken in most of them.
    """
    if old.permission_mode == new.permission_mode:
        return
    logger.debug(
        "AppState.permission_mode %s -> %s — notifying CCR + SDK",
        old.permission_mode,
        new.permission_mode,
    )
    if _session_metadata_listener is not None:
        try:
            _session_metadata_listener({"permission_mode": new.permission_mode})
        except Exception:
            logger.exception("session_metadata_listener raised")
    if _permission_mode_listener is not None:
        try:
            _permission_mode_listener(new.permission_mode)
        except Exception:
            logger.exception("permission_mode_listener raised")


def _on_initial_message_change(old: AppState, new: AppState) -> None:
    """``initial_message`` is consumed by the REPL on startup; no
    centralized side effect needed."""
    # Intentional no-op — the REPL reads ``initial_message`` directly via
    # the store's ``get_state`` and clears it after processing.
    return


def _on_advisor_model_change(old: AppState, new: AppState) -> None:
    """Persist advisor_model to user settings + invalidate the read cache.

    Mirrors TS ``commands/advisor.ts:49`` (``updateSettingsForSource(
    'userSettings', { advisorModel })``). The persistence pattern matches
    the existing TODO on ``_on_main_loop_model_change`` — settings are
    layered as ``global > project > local``; we write the user-scoped
    global level only. After persisting, the in-memory settings cache must
    be invalidated so the next ``get_settings()`` call (which
    ``_call_model_sync`` makes per-turn) reflects the new value
    immediately. Without the invalidate, mid-session toggles would only
    take effect after a process restart.
    """
    if old.advisor_model == new.advisor_model:
        return
    # Local imports avoid making this module's import time pay for the
    # settings stack on cold start. Use the SHARED default ConfigManager
    # so callers reading via ``load_config()`` / the global cache see
    # the new value, not a stale in-memory snapshot from before the
    # write.
    from src import config as cfg_mod
    from src.settings.settings import invalidate_settings_cache
    try:
        mgr = cfg_mod._get_default_manager()
        cfg = mgr.load_global()
        settings_section = cfg.get("settings")
        if not isinstance(settings_section, dict):
            settings_section = {}
        # None / "" both map to "unset" — write empty string for
        # round-trip fidelity with the SettingsSchema default.
        settings_section["advisor_model"] = new.advisor_model or ""
        cfg["settings"] = settings_section
        mgr.save_global(cfg)
        invalidate_settings_cache()
        logger.debug(
            "AppState.advisor_model %s -> %s — persisted + cache invalidated",
            old.advisor_model,
            new.advisor_model,
        )
    except Exception:
        # The slash command already reported success to the user based on
        # the in-memory store update; surface persistence failures via the
        # log but don't propagate (the in-memory change still works for
        # the current process; only re-launches would lose the setting).
        logger.exception(
            "Failed to persist advisor_model to settings; in-memory value still active"
        )


# Registry. EVERY field in AppState must appear here as a handler
# (function-form, including explicit no-ops). The coverage test enforces
# this — adding a new AppState field without a handler entry fails
# ``test_every_field_appears_in_handler_registry``.
_FIELD_HANDLERS: dict[str, Callable[[AppState, AppState], None]] = {
    "main_loop_model": _on_main_loop_model_change,
    "verbose": _on_verbose_change,
    "expanded_view": _on_expanded_view_change,
    "permission_mode": _on_permission_mode_change,
    "initial_message": _on_initial_message_change,
    "advisor_model": _on_advisor_model_change,
}


def on_change_app_state(old_state: AppState, new_state: AppState) -> None:
    """Route the diff to each field's handler.

    Mirrors TS ``onChangeAppState`` (``state/onChangeAppState.ts``). Each
    handler is responsible for checking ``old == new`` and returning early
    when the field didn't change — this means a single ``setState`` that
    mutates multiple fields fires all relevant handlers exactly once.
    """
    for fname in (f.name for f in dc_fields(AppState)):
        handler = _FIELD_HANDLERS.get(fname)
        if handler is None:
            continue
        try:
            handler(old_state, new_state)
        except Exception:
            logger.exception(
                "on_change_app_state handler for %r raised; continuing",
                fname,
            )


# ---------------------------------------------------------------------------
# Store factory
# ---------------------------------------------------------------------------


def create_app_state_store(
    initial: AppState | None = None,
) -> Store[AppState]:
    """Construct an AppState store with the centralized side-effect router.

    Equivalent to TS:
    ``createStore(getDefaultAppState(), onChangeAppState)``.
    """
    return create_store(
        initial if initial is not None else get_default_app_state(),
        on_change=on_change_app_state,
    )


__all__ = [
    "AppState",
    "create_app_state_store",
    "get_default_app_state",
    "on_change_app_state",
    "replace_state",
    "set_permission_mode_listener",
    "set_session_metadata_listener",
]

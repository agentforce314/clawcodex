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

    # Provider key (matches ``~/.clawcodex/config.json``'s providers map)
    # that ``main_loop_model`` belongs to. Persisted alongside the model
    # (#280): clawcodex is multi-provider and a persisted model is only
    # restorable when the next launch uses the same provider (the
    # advisor_model/advisor_provider precedent). Written together with
    # main_loop_model via ``persist_model_choice``.
    main_loop_provider: str | None = None

    # Verbose mode (TS: AppStateStore.ts:92)
    verbose: bool = False

    # Expanded view: 'none' | 'tasks' | 'teammates' (TS: AppStateStore.ts:96)
    expanded_view: str = "none"

    # Permission mode (slice of toolPermissionContext — TS: AppStateStore.ts:110).
    # Stored as a string here to avoid a tight coupling with the existing
    # permissions package's enums; the bridge handlers can normalize.
    # This field is the intended single source of truth for permission mode
    # (``/permissions`` writes it via the reactive store). When the wiring
    # chapter connects it to real tool gating, derive ``ToolPermissionContext``
    # from here — do NOT fork a second ``permission_mode`` onto the TUI
    # render-state ``src.tui.state.AppState``.
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

    # Provider key (matches ``~/.clawcodex/config.json``'s providers map)
    # for the advisor model. REQUIRED when advisor_model is set —
    # see ``src/settings/types.py:advisor_provider`` for the rationale.
    # Persisted via ``_on_advisor_provider_change`` alongside advisor_model.
    advisor_provider: str | None = None

    # Force client-side advisor mode. False = auto (server-side when
    # possible, client-side otherwise). True = always client-side even
    # on 1P Anthropic (lets users pair a non-Anthropic advisor with an
    # Anthropic main loop, or get transparency on the advisor call).
    # Persisted to ``settings.advisor_client_mode`` via
    # ``_on_advisor_client_mode_change``.
    advisor_client_mode: bool = False


def replace_state(state: AppState, **changes: Any) -> AppState:
    """Return a copy of ``state`` with ``changes`` applied. Equivalent
    to ``dataclasses.replace(state, ...)`` — exists as a named helper
    to keep the call sites readable."""
    return dataclasses.replace(state, **changes)


def get_default_app_state() -> AppState:
    """Mirror of TS ``getDefaultAppState`` (``AppStateStore.ts:458``).

    Display preferences persisted by the side-effect router (#280)
    are seeded back here so /verbose and the expanded-view toggle
    survive restarts. ``main_loop_model`` is deliberately NOT seeded:
    entrypoints own model resolution (cli option > settings.model >
    provider default_model) and pass it via ``provider.model``.
    """
    state = AppState()
    try:
        from src.settings.settings import get_settings

        settings = get_settings()
        if settings.verbose:
            state = replace_state(state, verbose=True)
        if settings.expanded_view in ("none", "tasks", "teammates"):
            state = replace_state(state, expanded_view=settings.expanded_view)
    except Exception:
        # Settings load failures must not block startup; defaults apply.
        logger.debug("could not seed AppState from settings", exc_info=True)
    return state


# ---------------------------------------------------------------------------
# Side-effect handlers (the on_change_app_state router)
# ---------------------------------------------------------------------------


def _persist_user_settings(**values: Any) -> None:
    """Write ``values`` into the user-scoped global config's ``settings``
    section and invalidate the read cache — the single writer chokepoint
    (#280; TS ``updateSettingsForSource('userSettings', ...)``).

    Failures are logged, never propagated: the in-memory store update
    already took effect for this process; only re-launches would lose
    the setting.
    """
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
        # Copy before mutating: load_global returns a shallow copy, so an
        # in-place update would leak unsaved values into the manager's
        # cache if save_global fails.
        if isinstance(settings_section, dict):
            settings_section = dict(settings_section)
        else:
            settings_section = {}
        settings_section.update(values)
        cfg["settings"] = settings_section
        mgr.save_global(cfg)
        invalidate_settings_cache()
        logger.debug("persisted user settings %s + cache invalidated", values)
    except Exception:
        logger.exception(
            "Failed to persist %s to settings; in-memory value still active",
            sorted(values.keys()),
        )


def _on_main_loop_model_change(old: AppState, new: AppState) -> None:
    """Mirror model choice into bootstrap singleton + persist (#280).

    Matches TS at ``onChangeAppState.ts:97-120`` — when the user changes
    the model (via /model slash command or the model picker), the
    bootstrap-state override must update so the next API call reads the
    new value, and settings persistence happens as a side effect.
    ``settings.model`` is the restore channel: entrypoints resolve the
    startup model as ``cli option > settings.model > provider
    default_model``.
    """
    if old.main_loop_model == new.main_loop_model:
        return
    set_main_loop_model_override(new.main_loop_model)
    logger.debug(
        "AppState.main_loop_model %s -> %s — mirrored to bootstrap",
        old.main_loop_model,
        new.main_loop_model,
    )
    _persist_user_settings(model=new.main_loop_model or "")


def _on_main_loop_provider_change(old: AppState, new: AppState) -> None:
    """Persist the provider key paired with the model choice (#280).

    ``get_persisted_model`` only restores a model whose persisted
    provider matches the launch provider — without the pairing, a model
    persisted on provider A would feed provider B an invalid model id
    on the next launch.
    """
    if old.main_loop_provider == new.main_loop_provider:
        return
    _persist_user_settings(model_provider=new.main_loop_provider or "")


def persist_model_choice(
    store: Any, provider_name: str | None, model: str | None
) -> None:
    """Single write path for a user model choice (#280).

    When a reactive store is wired, write through it so the side-effect
    router persists (and mirrors bootstrap); otherwise persist directly
    via the same chokepoint the handlers use — the ``/advisor`` command
    pattern (``builtins._persist_advisor_model``).
    """
    if store is not None:
        store.set_state(
            lambda s: replace_state(
                s,
                main_loop_model=model or None,
                main_loop_provider=provider_name or None,
            )
        )
        return
    set_main_loop_model_override(model or None)
    _persist_user_settings(
        model=model or "", model_provider=provider_name or ""
    )


def _on_verbose_change(old: AppState, new: AppState) -> None:
    if old.verbose == new.verbose:
        return
    logger.debug("AppState.verbose %s -> %s", old.verbose, new.verbose)
    _persist_user_settings(verbose=bool(new.verbose))


def _on_expanded_view_change(old: AppState, new: AppState) -> None:
    if old.expanded_view == new.expanded_view:
        return
    logger.debug(
        "AppState.expanded_view %s -> %s — persisted",
        old.expanded_view,
        new.expanded_view,
    )
    # Persisted as the store's own 'none' | 'tasks' | 'teammates' string
    # rather than the TS legacy showExpandedTodos/showSpinnerTree boolean
    # pair (this port's config file is not shared with TS Claude Code).
    _persist_user_settings(expanded_view=new.expanded_view or "none")


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
    # None / "" both map to "unset" — write empty string for round-trip
    # fidelity with the SettingsSchema default.
    _persist_user_settings(advisor_model=new.advisor_model or "")


def _on_advisor_provider_change(old: AppState, new: AppState) -> None:
    """Persist advisor_provider to user settings + invalidate the read cache.

    Same persistence pattern as :func:`_on_advisor_model_change`. Written
    alongside advisor_model by the /advisor command (both fields are part
    of the ``<provider>:<model>`` argument and move together). Surfaces
    as ``settings.advisor_provider`` for the read channel; see
    ``src/utils/advisor.py`` for the consumer side.
    """
    if old.advisor_provider == new.advisor_provider:
        return
    _persist_user_settings(advisor_provider=new.advisor_provider or "")


def _on_advisor_client_mode_change(old: AppState, new: AppState) -> None:
    """Persist advisor_client_mode to user settings + invalidate the read cache.

    Same persistence pattern as ``_on_advisor_model_change`` — writes the
    boolean into the user-scoped global config and invalidates the cache
    so the next ``_call_model_sync`` turn picks up the new value without
    a process restart.
    """
    if old.advisor_client_mode == new.advisor_client_mode:
        return
    _persist_user_settings(advisor_client_mode=bool(new.advisor_client_mode))


# Registry. EVERY field in AppState must appear here as a handler
# (function-form, including explicit no-ops). The coverage test enforces
# this — adding a new AppState field without a handler entry fails
# ``test_every_field_appears_in_handler_registry``.
_FIELD_HANDLERS: dict[str, Callable[[AppState, AppState], None]] = {
    "main_loop_model": _on_main_loop_model_change,
    "main_loop_provider": _on_main_loop_provider_change,
    "verbose": _on_verbose_change,
    "expanded_view": _on_expanded_view_change,
    "permission_mode": _on_permission_mode_change,
    "initial_message": _on_initial_message_change,
    "advisor_model": _on_advisor_model_change,
    "advisor_provider": _on_advisor_provider_change,
    "advisor_client_mode": _on_advisor_client_mode_change,
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
    "persist_model_choice",
    "replace_state",
    "set_permission_mode_listener",
    "set_session_metadata_listener",
]

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
    # This field is the intended single source of truth for permission mode
    # (``/permissions`` writes it via the reactive store). When the wiring
    # chapter connects it to real tool gating, derive ``ToolPermissionContext``
    # from here — do NOT fork a second ``permission_mode`` onto any other
    # render-state.
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

    # Master on/off switch for the advisor. Default False — the advisor is
    # OFF unless explicitly enabled (config flag ``advisor_enabled`` or
    # ``/advisor <provider>:<model>``). Persisted to ``settings.advisor_enabled``
    # via ``_on_advisor_enabled_change``.
    advisor_enabled: bool = False


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


# Active-provider supplier slot (ch03 round-3 G1). Pattern matches the
# listener slots below: entrypoints register a LIVE attribute read
# (``lambda: self.provider_name``) so a mid-session provider reinit
# (repl/core.py provider-switch flow) is reflected at persist time —
# a captured string would persist /model choices under a stale provider
# and defeat the (model, provider) pair guard.
_active_provider_supplier: Callable[[], str | None] | None = None


def set_active_provider_supplier(
    cb: Callable[[], str | None] | None,
) -> None:
    global _active_provider_supplier
    _active_provider_supplier = cb


def _active_provider_name() -> str:
    if _active_provider_supplier is None:
        return ""
    try:
        return _active_provider_supplier() or ""
    except Exception:
        return ""


def _persist_settings_keys(**keys: Any) -> None:
    """Write keys into the global config's ``settings`` sub-key.

    The advisor write idiom (shared default ConfigManager → save_global →
    invalidate_settings_cache) hoisted for reuse by the model handler.
    Raises on failure — CALLERS swallow and log, so the in-memory change
    still works for the current process.
    """
    from src import config as cfg_mod
    from src.settings.settings import invalidate_settings_cache

    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    settings_section = cfg.get("settings")
    if not isinstance(settings_section, dict):
        settings_section = {}
    settings_section.update(keys)
    cfg["settings"] = settings_section
    mgr.save_global(cfg)
    invalidate_settings_cache()


def _on_main_loop_model_change(old: AppState, new: AppState) -> None:
    """Mirror model choice into bootstrap + persist the (model, provider)
    pair to settings.

    Matches TS at ``onChangeAppState.ts:97-120`` (persist to user settings
    + ``setMainLoopModelOverride``; TS also syncs the active provider
    profile at ``:117-119`` — provider-profile sync is ch04 client
    territory here). Unset convention: ``None`` model writes ``""`` for
    BOTH keys — idiom consistency with the advisor handlers (NOT TS's
    ``model: undefined`` key-removal; read-equivalent through the
    defaults merge in ``load_settings``).
    """
    if old.main_loop_model == new.main_loop_model:
        return
    set_main_loop_model_override(new.main_loop_model)
    logger.debug(
        "AppState.main_loop_model %s -> %s — mirrored to bootstrap",
        old.main_loop_model,
        new.main_loop_model,
    )
    try:
        _persist_settings_keys(
            model=new.main_loop_model or "",
            model_provider=(
                _active_provider_name() if new.main_loop_model else ""
            ),
        )
    except Exception:
        logger.exception(
            "Failed to persist model to settings; in-memory value still active"
        )


def _persist_config_keys(**keys: Any) -> None:
    """Write top-level global-config keys (TS global config, not settings)."""
    from src import config as cfg_mod

    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    cfg.update(keys)
    mgr.save_global(cfg)


def _on_verbose_change(old: AppState, new: AppState) -> None:
    """Persist ``verbose`` to the global config top level (TS
    ``onChangeAppState.ts:144-148`` — config, not settings)."""
    if old.verbose == new.verbose:
        return
    logger.debug("AppState.verbose %s -> %s", old.verbose, new.verbose)
    try:
        _persist_config_keys(verbose=bool(new.verbose))
    except Exception:
        logger.exception("Failed to persist verbose to global config")


# expanded_view domain is 'none' | 'tasks' | 'teammates' (AppStateStore.ts:96).
# On-disk form is TS's legacy boolean pair; read-back priority is TS-exact
# (main.tsx:2932): showSpinnerTree ? 'teammates' : showExpandedTodos ?
# 'tasks' : 'none' — a (True, True) disk state reads as 'teammates'.
_EXPANDED_VIEW_TO_BOOLS: dict[str, tuple[bool, bool]] = {
    "none": (False, False),
    "tasks": (True, False),
    "teammates": (False, True),
}


def expanded_view_from_config_bools(
    show_expanded_todos: bool, show_spinner_tree: bool
) -> str:
    if show_spinner_tree:
        return "teammates"
    if show_expanded_todos:
        return "tasks"
    return "none"


def _on_expanded_view_change(old: AppState, new: AppState) -> None:
    """Persist ``expanded_view`` to the global config as the legacy
    showExpandedTodos + showSpinnerTree pair (TS onChangeAppState.ts:123-136)."""
    if old.expanded_view == new.expanded_view:
        return
    logger.debug(
        "AppState.expanded_view %s -> %s — persist as showExpandedTodos/showSpinnerTree",
        old.expanded_view,
        new.expanded_view,
    )
    todos, spinner = _EXPANDED_VIEW_TO_BOOLS.get(
        new.expanded_view, (False, False)
    )
    try:
        _persist_config_keys(
            showExpandedTodos=todos, showSpinnerTree=spinner
        )
    except Exception:
        logger.exception("Failed to persist expanded_view to global config")


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
    from src import config as cfg_mod
    from src.settings.settings import invalidate_settings_cache
    try:
        mgr = cfg_mod._get_default_manager()
        cfg = mgr.load_global()
        settings_section = cfg.get("settings")
        if not isinstance(settings_section, dict):
            settings_section = {}
        settings_section["advisor_provider"] = new.advisor_provider or ""
        cfg["settings"] = settings_section
        mgr.save_global(cfg)
        invalidate_settings_cache()
        logger.debug(
            "AppState.advisor_provider %s -> %s — persisted + cache invalidated",
            old.advisor_provider,
            new.advisor_provider,
        )
    except Exception:
        logger.exception(
            "Failed to persist advisor_provider to settings; in-memory value still active"
        )


def _on_advisor_client_mode_change(old: AppState, new: AppState) -> None:
    """Persist advisor_client_mode to user settings + invalidate the read cache.

    Same persistence pattern as ``_on_advisor_model_change`` — writes the
    boolean into the user-scoped global config and invalidates the cache
    so the next ``_call_model_sync`` turn picks up the new value without
    a process restart.
    """
    if old.advisor_client_mode == new.advisor_client_mode:
        return
    from src import config as cfg_mod
    from src.settings.settings import invalidate_settings_cache
    try:
        mgr = cfg_mod._get_default_manager()
        cfg = mgr.load_global()
        settings_section = cfg.get("settings")
        if not isinstance(settings_section, dict):
            settings_section = {}
        settings_section["advisor_client_mode"] = bool(new.advisor_client_mode)
        cfg["settings"] = settings_section
        mgr.save_global(cfg)
        invalidate_settings_cache()
        logger.debug(
            "AppState.advisor_client_mode %s -> %s — persisted + cache invalidated",
            old.advisor_client_mode,
            new.advisor_client_mode,
        )
    except Exception:
        logger.exception(
            "Failed to persist advisor_client_mode to settings; in-memory value still active"
        )


def _on_advisor_enabled_change(old: AppState, new: AppState) -> None:
    """Persist the advisor master switch to user settings + invalidate the read
    cache. Same persistence pattern as ``_on_advisor_client_mode_change``."""
    if old.advisor_enabled == new.advisor_enabled:
        return
    from src import config as cfg_mod
    from src.settings.settings import invalidate_settings_cache
    try:
        mgr = cfg_mod._get_default_manager()
        cfg = mgr.load_global()
        settings_section = cfg.get("settings")
        if not isinstance(settings_section, dict):
            settings_section = {}
        settings_section["advisor_enabled"] = bool(new.advisor_enabled)
        cfg["settings"] = settings_section
        mgr.save_global(cfg)
        invalidate_settings_cache()
    except Exception:
        logger.exception(
            "Failed to persist advisor_enabled to settings; in-memory value still active"
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
    "advisor_provider": _on_advisor_provider_change,
    "advisor_client_mode": _on_advisor_client_mode_change,
    "advisor_enabled": _on_advisor_enabled_change,
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


def seed_app_state_from_settings(active_provider: str | None) -> AppState:
    """Read-side of the §3.4 persistence (ch03 round-3 G1).

    TS seeds ``verbose`` from global config (``main.tsx:1129``, ``:2928``)
    and ``expandedView`` from the showSpinnerTree/showExpandedTodos pair
    (``:2932``); the model read mechanism is ``getUserSpecifiedModelSetting``
    (``utils/model/model.ts:109-135``) whose provider-match guard this
    mirrors: the persisted model applies ONLY when it was persisted under
    the session's active provider — a stale cross-provider model must
    never fire at the wrong endpoint.

    NB ``get_settings()`` merges project/local "settings" sub-keys, so a
    repo could shadow ``model`` — bounded by the provider-match guard and
    by model choice being non-credential-bearing (the settings-tier trust
    policy is ch15/16 work; ch02's untrusted strip covers
    env/providers/default_provider).
    """
    from src import config as cfg_mod
    from src.settings.settings import get_settings

    try:
        settings = get_settings()
        cfg = cfg_mod._get_default_manager().load_global()
    except Exception:
        logger.exception("settings seed failed; starting from defaults")
        return get_default_app_state()

    model: str | None = settings.model or None
    # Provider-mismatch guard: the persisted provider must be truthy AND
    # match — a pair persisted with model_provider="" (unregistered
    # supplier) is never applied, honoring the fail-safe contract.
    if model and (
        not settings.model_provider
        or settings.model_provider != (active_provider or "")
    ):
        model = None
    if model:
        # Keep the bootstrap mirror consistent from the first read.
        set_main_loop_model_override(model)

    return AppState(
        main_loop_model=model,
        verbose=bool(cfg.get("verbose", False)),
        expanded_view=expanded_view_from_config_bools(
            bool(cfg.get("showExpandedTodos", False)),
            bool(cfg.get("showSpinnerTree", False)),
        ),
    )


def create_app_state_store(
    initial: AppState | None = None,
    *,
    active_provider: str | None = None,
) -> Store[AppState]:
    """Construct an AppState store with the centralized side-effect router.

    Equivalent to TS:
    ``createStore(getDefaultAppState(), onChangeAppState)`` — plus the
    settings read-side: when ``initial`` is not supplied, the state is
    seeded from persisted settings/config (model gated on
    ``active_provider``; see :func:`seed_app_state_from_settings`).
    """
    if initial is None:
        initial = seed_app_state_from_settings(active_provider)
    return create_store(initial, on_change=on_change_app_state)


__all__ = [
    "AppState",
    "create_app_state_store",
    "expanded_view_from_config_bools",
    "get_default_app_state",
    "on_change_app_state",
    "replace_state",
    "seed_app_state_from_settings",
    "set_active_provider_supplier",
    "set_permission_mode_listener",
    "set_session_metadata_listener",
]

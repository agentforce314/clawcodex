"""ch03 round-3 G1: AppState handler persistence + read-side seeding.

The §3.4 chokepoint made real: model/verbose/expanded_view changes persist
on diff (TS onChangeAppState.ts:97-148), and store creation seeds back from
the persisted values — with the (model, model_provider) pair guard
(TS utils/model/model.ts:109-135) so a stale cross-provider model never
applies.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

import pytest

import src.config as config_module
from src.bootstrap.state import (
    get_main_loop_model_override,
    reset_state_for_tests,
)
from src.settings.settings import (
    apply_persisted_model,
    get_settings,
    invalidate_settings_cache,
)
from src.state.app_state import (
    AppState,
    create_app_state_store,
    expanded_view_from_config_bools,
    replace_state,
    seed_app_state_from_settings,
    set_active_provider_supplier,
)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    global_cfg = tmp_path / ".clawcodex" / "config.json"
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_FILE", global_cfg)
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_DIR", global_cfg.parent)
    monkeypatch.setattr(config_module, "_default_manager", None, raising=False)
    monkeypatch.setattr(config_module, "_find_git_root", lambda *a, **k: None)
    invalidate_settings_cache()
    reset_state_for_tests()
    set_active_provider_supplier(None)
    yield global_cfg
    invalidate_settings_cache()
    reset_state_for_tests()
    set_active_provider_supplier(None)


def _read_global(path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


# ---------------------------------------------------------------------------
# Write side — handler persistence
# ---------------------------------------------------------------------------


def test_model_change_persists_pair_and_mirrors_bootstrap(_isolated_state):
    set_active_provider_supplier(lambda: "openrouter")
    store = create_app_state_store(AppState())
    store.set_state(lambda s: replace_state(s, main_loop_model="kimi-k3"))

    assert get_main_loop_model_override() == "kimi-k3"
    invalidate_settings_cache()
    s = get_settings()
    assert s.model == "kimi-k3"
    assert s.model_provider == "openrouter"


def test_model_none_writes_empty_strings(_isolated_state):
    set_active_provider_supplier(lambda: "openrouter")
    store = create_app_state_store(AppState(main_loop_model="kimi-k3"))
    store.set_state(lambda s: replace_state(s, main_loop_model=None))

    invalidate_settings_cache()
    s = get_settings()
    assert s.model == ""
    assert s.model_provider == ""


def test_model_persists_empty_provider_when_supplier_unregistered(
    _isolated_state,
):
    store = create_app_state_store(AppState())
    store.set_state(lambda s: replace_state(s, main_loop_model="kimi-k3"))
    invalidate_settings_cache()
    s = get_settings()
    assert s.model == "kimi-k3"
    assert s.model_provider == ""


def test_model_write_does_not_touch_provider_selection(_isolated_state):
    # Critic note-1: /model must not mutate the active-provider selection.
    global_cfg = _isolated_state
    global_cfg.parent.mkdir(parents=True, exist_ok=True)
    global_cfg.write_text(json.dumps({"default_provider": "anthropic"}))
    config_module._default_manager = None

    set_active_provider_supplier(lambda: "openrouter")
    store = create_app_state_store(AppState())
    store.set_state(lambda s: replace_state(s, main_loop_model="kimi-k3"))

    data = _read_global(global_cfg)
    assert data.get("default_provider") == "anthropic"
    assert "provider" not in data.get("settings", {})


def test_verbose_persists_to_config_top_level(_isolated_state):
    store = create_app_state_store(AppState())
    store.set_state(lambda s: replace_state(s, verbose=True))
    data = _read_global(_isolated_state)
    assert data.get("verbose") is True


def test_expanded_view_persists_as_legacy_boolean_pair(_isolated_state):
    store = create_app_state_store(AppState())
    store.set_state(lambda s: replace_state(s, expanded_view="tasks"))
    data = _read_global(_isolated_state)
    assert data.get("showExpandedTodos") is True
    assert data.get("showSpinnerTree") is False

    store.set_state(lambda s: replace_state(s, expanded_view="teammates"))
    data = _read_global(_isolated_state)
    assert data.get("showExpandedTodos") is False
    assert data.get("showSpinnerTree") is True

    store.set_state(lambda s: replace_state(s, expanded_view="none"))
    data = _read_global(_isolated_state)
    assert data.get("showExpandedTodos") is False
    assert data.get("showSpinnerTree") is False


# ---------------------------------------------------------------------------
# Read side — seeding + the pair guard
# ---------------------------------------------------------------------------


def _persist(global_cfg, *, settings=None, **top):
    data = dict(top)
    if settings:
        data["settings"] = settings
    global_cfg.parent.mkdir(parents=True, exist_ok=True)
    global_cfg.write_text(json.dumps(data))
    config_module._default_manager = None
    invalidate_settings_cache()


def test_seed_applies_model_when_provider_matches(_isolated_state):
    _persist(
        _isolated_state,
        settings={"model": "kimi-k3", "model_provider": "openrouter"},
    )
    state = seed_app_state_from_settings("openrouter")
    assert state.main_loop_model == "kimi-k3"
    assert get_main_loop_model_override() == "kimi-k3"


def test_seed_ignores_model_on_provider_mismatch(_isolated_state):
    # Critic note-1 vacuous-guard case: pair persisted under provider A,
    # session active under provider B -> model ignored, selection untouched.
    _persist(
        _isolated_state,
        default_provider="anthropic",
        settings={"model": "kimi-k3", "model_provider": "openrouter"},
    )
    state = seed_app_state_from_settings("anthropic")
    assert state.main_loop_model is None
    assert get_main_loop_model_override() is None
    assert _read_global(_isolated_state).get("default_provider") == "anthropic"


def test_seed_reads_verbose_and_expanded_view(_isolated_state):
    _persist(
        _isolated_state,
        verbose=True,
        showExpandedTodos=False,
        showSpinnerTree=True,
    )
    state = seed_app_state_from_settings(None)
    assert state.verbose is True
    assert state.expanded_view == "teammates"


def test_expanded_view_read_priority_spinner_wins_ties(_isolated_state):
    # (True, True) on disk reads as 'teammates' (TS main.tsx:2932 priority).
    assert expanded_view_from_config_bools(True, True) == "teammates"
    assert expanded_view_from_config_bools(True, False) == "tasks"
    assert expanded_view_from_config_bools(False, False) == "none"


def test_store_factory_seeds_when_initial_omitted(_isolated_state):
    _persist(
        _isolated_state,
        verbose=True,
        settings={"model": "kimi-k3", "model_provider": "openrouter"},
    )
    store = create_app_state_store(active_provider="openrouter")
    assert store.get_state().main_loop_model == "kimi-k3"
    assert store.get_state().verbose is True


# ---------------------------------------------------------------------------
# Effective read side — apply_persisted_model
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self) -> None:
        self.model = "default-model"


def test_apply_persisted_model_match(_isolated_state):
    _persist(
        _isolated_state,
        settings={"model": "kimi-k3", "model_provider": "openrouter"},
    )
    provider = _FakeProvider()
    assert apply_persisted_model(provider, "openrouter") is True
    assert provider.model == "kimi-k3"


def test_apply_persisted_model_mismatch_or_unset(_isolated_state):
    _persist(
        _isolated_state,
        settings={"model": "kimi-k3", "model_provider": "openrouter"},
    )
    provider = _FakeProvider()
    assert apply_persisted_model(provider, "anthropic") is False
    assert provider.model == "default-model"

    _persist(_isolated_state, settings={})
    provider2 = _FakeProvider()
    assert apply_persisted_model(provider2, "openrouter") is False
    assert provider2.model == "default-model"


def test_restart_round_trip_same_provider(_isolated_state):
    """DoD-2: a /model choice survives 'restart' on the same provider and
    is ignored after a provider switch."""
    set_active_provider_supplier(lambda: "openrouter")
    store = create_app_state_store(AppState())
    store.set_state(lambda s: replace_state(s, main_loop_model="kimi-k3"))

    # "Restart": fresh seed + fresh provider, same provider name.
    reset_state_for_tests()
    set_active_provider_supplier(None)
    invalidate_settings_cache()
    config_module._default_manager = None
    provider = _FakeProvider()
    assert apply_persisted_model(provider, "openrouter") is True
    assert provider.model == "kimi-k3"
    state = seed_app_state_from_settings("openrouter")
    assert state.main_loop_model == "kimi-k3"

    # Provider switch: persisted pair ignored everywhere.
    provider_b = _FakeProvider()
    assert apply_persisted_model(provider_b, "anthropic") is False
    state_b = seed_app_state_from_settings("anthropic")
    assert state_b.main_loop_model is None


if __name__ == "__main__":
    unittest.main()

"""Tests for the spinner-verb pool and its wiring into the status verb.

Covers the port of ``typescript/src/constants/spinnerVerbs.ts``:
* the verbatim ``SPINNER_VERBS`` pool,
* ``get_spinner_verbs`` settings-merge semantics (``append`` / ``replace``),
* ``pick_spinner_verb`` (pool member + empty-pool fallback),
* and the live wiring: ``AppState.set_thinking`` samples the pool when no
  explicit verb is supplied (replacing the old hardcoded ``"Synthesizing"``).
"""

from __future__ import annotations

import pytest

from src.constants.spinner_verbs import (
    SPINNER_VERBS,
    get_spinner_verbs,
    pick_spinner_verb,
)
from src.settings.types import SettingsSchema, SpinnerVerbsSettings


# --------------------------------------------------------------------------
# SPINNER_VERBS pool
# --------------------------------------------------------------------------

def test_pool_is_nonempty_and_verbatim_anchors():
    # 187 entries ported verbatim from spinnerVerbs.ts:16-204.
    assert len(SPINNER_VERBS) == 187
    # Anchors spanning the list, incl. the apostrophe + accented entries.
    for anchor in ("Accomplishing", "Cogitating", "Synthesizing",
                   "Working", "Zigzagging", "Beboppin'",
                   "Flambéing", "Sautéing"):
        assert anchor in SPINNER_VERBS, anchor


def test_pool_has_no_duplicates():
    assert len(set(SPINNER_VERBS)) == len(SPINNER_VERBS)


# --------------------------------------------------------------------------
# get_spinner_verbs — settings merge
# --------------------------------------------------------------------------

def _patch_settings(monkeypatch, spinner_verbs):
    """Point get_spinner_verbs' lazy ``get_settings`` at a stub schema."""
    schema = SettingsSchema(spinner_verbs=spinner_verbs)
    monkeypatch.setattr(
        "src.settings.settings.get_settings", lambda *a, **k: schema
    )


def test_get_spinner_verbs_defaults_when_unset(monkeypatch):
    _patch_settings(monkeypatch, None)
    assert get_spinner_verbs() == list(SPINNER_VERBS)


def test_get_spinner_verbs_append(monkeypatch):
    _patch_settings(
        monkeypatch, SpinnerVerbsSettings(mode="append", verbs=["Frobnicating"])
    )
    result = get_spinner_verbs()
    assert result[: len(SPINNER_VERBS)] == list(SPINNER_VERBS)
    assert result[-1] == "Frobnicating"
    assert len(result) == len(SPINNER_VERBS) + 1


def test_get_spinner_verbs_replace(monkeypatch):
    _patch_settings(
        monkeypatch,
        SpinnerVerbsSettings(mode="replace", verbs=["Frobnicating", "Wibbling"]),
    )
    assert get_spinner_verbs() == ["Frobnicating", "Wibbling"]


def test_get_spinner_verbs_replace_empty_falls_back(monkeypatch):
    # TS guards empty-replace by returning defaults.
    _patch_settings(monkeypatch, SpinnerVerbsSettings(mode="replace", verbs=[]))
    assert get_spinner_verbs() == list(SPINNER_VERBS)


def test_get_spinner_verbs_ignores_malformed_non_list_verbs(monkeypatch):
    # A bare string (not a list) must not char-explode into single letters.
    _patch_settings(
        monkeypatch, SpinnerVerbsSettings(mode="append", verbs="Vibing")  # type: ignore[arg-type]
    )
    assert get_spinner_verbs() == list(SPINNER_VERBS)


def test_get_spinner_verbs_survives_settings_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr("src.settings.settings.get_settings", _boom)
    # Never raises into the render path — defaults stand.
    assert get_spinner_verbs() == list(SPINNER_VERBS)


# --------------------------------------------------------------------------
# pick_spinner_verb
# --------------------------------------------------------------------------

def test_pick_returns_pool_member(monkeypatch):
    _patch_settings(monkeypatch, None)
    for _ in range(20):
        assert pick_spinner_verb() in SPINNER_VERBS


def test_pick_empty_pool_falls_back_to_working(monkeypatch):
    monkeypatch.setattr(
        "src.constants.spinner_verbs.get_spinner_verbs", lambda: []
    )
    assert pick_spinner_verb() == "Working"


# --------------------------------------------------------------------------
# Wiring: AppState.set_thinking samples the pool
# --------------------------------------------------------------------------

def test_set_thinking_samples_pool_when_no_verb(monkeypatch):
    from src.tui.state import AppState

    monkeypatch.setattr(
        "src.tui.state.pick_spinner_verb", lambda: "Frolicking"
    )
    state = AppState()
    state.set_thinking(True)
    assert state.verb == "Frolicking"


def test_set_thinking_explicit_verb_still_wins(monkeypatch):
    from src.tui.state import AppState

    monkeypatch.setattr(
        "src.tui.state.pick_spinner_verb", lambda: "Frolicking"
    )
    state = AppState()
    state.set_thinking(True, verb="Compiling")
    assert state.verb == "Compiling"


def test_set_thinking_false_resets_to_ready():
    from src.tui.state import AppState

    state = AppState()
    state.set_thinking(True, verb="Compiling")
    state.set_thinking(False)
    assert state.verb == "Ready"

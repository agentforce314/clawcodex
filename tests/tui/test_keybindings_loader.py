"""Tests for ``src.tui.keybindings_loader`` (WI-2.2)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.tui.keybindings_loader import (
    DEFAULT_BINDINGS,
    KEY_VERSIONS_RECOGNIZED,
    load_user_bindings,
    merge_bindings,
)
from src.tui.keybindings_schema import KeybindingEntry


# ------------------------------------------------------------------
# merge_bindings (pure)
# ------------------------------------------------------------------


def test_no_user_entries_returns_defaults_unchanged() -> None:
    merged = merge_bindings(DEFAULT_BINDINGS, [])
    assert merged == list(DEFAULT_BINDINGS)


def test_user_override_replaces_default_with_same_action() -> None:
    """Override: user remaps ``transcript.top`` from ``g g`` to ``ctrl+home``."""

    user = [
        KeybindingEntry(action="transcript.top", keys=("ctrl+home",)),
    ]
    merged = merge_bindings(DEFAULT_BINDINGS, user)
    actions_to_keys = {e.action: e.keys for e in merged}
    assert actions_to_keys["transcript.top"] == ("ctrl+home",)
    # Other defaults survive untouched.
    assert actions_to_keys["transcript.bottom"] == ("G",)


def test_user_only_action_is_appended() -> None:
    """Additive: user adds a new action not in defaults."""

    user = [KeybindingEntry(action="transcript.search.open", keys=("ctrl+f",))]
    merged = merge_bindings(DEFAULT_BINDINGS, user)
    actions = [e.action for e in merged]
    assert "transcript.search.open" in actions
    # All defaults still present.
    for default in DEFAULT_BINDINGS:
        assert default.action in actions


def test_user_can_have_multiple_entries_for_same_action() -> None:
    """Different ``when`` clauses for the same action — both survive."""

    user = [
        KeybindingEntry(
            action="transcript.top",
            keys=("ctrl+home",),
            when=None,
        ),
        KeybindingEntry(
            action="transcript.top",
            keys=("g", "g"),
            when="transcript.focused",
        ),
    ]
    merged = merge_bindings(DEFAULT_BINDINGS, user)
    top_entries = [e for e in merged if e.action == "transcript.top"]
    assert len(top_entries) == 2
    # The default for this action was dropped (user wins entirely).
    assert all(e in user for e in top_entries)


def test_merge_preserves_default_order_for_unmodified_actions() -> None:
    user = [KeybindingEntry(action="transcript.bottom", keys=("end",))]
    merged = merge_bindings(DEFAULT_BINDINGS, user)
    actions_in_order = [e.action for e in merged]
    # transcript.top should still appear before transcript.bottom — the
    # override doesn't reorder.
    assert actions_in_order.index("transcript.top") < actions_in_order.index(
        "transcript.bottom"
    )


# ------------------------------------------------------------------
# load_user_bindings — file system interactions
# ------------------------------------------------------------------


def test_no_file_returns_defaults_silently(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "does_not_exist.json"
    with caplog.at_level(logging.WARNING, logger="src.tui.keybindings_loader"):
        bindings = load_user_bindings(target)
    assert bindings == list(DEFAULT_BINDINGS)
    # No WARNING for the common case.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_malformed_json_falls_back_to_defaults_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "bindings.json"
    target.write_text("{ this is not valid JSON")
    with caplog.at_level(logging.WARNING, logger="src.tui.keybindings_loader"):
        bindings = load_user_bindings(target)
    assert bindings == list(DEFAULT_BINDINGS)
    assert any("Malformed JSON" in r.message for r in caplog.records)


def test_invalid_schema_falls_back_to_defaults_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "bindings.json"
    target.write_text(json.dumps({"version": 1, "bindings": [{"action": "x"}]}))
    with caplog.at_level(logging.WARNING, logger="src.tui.keybindings_loader"):
        bindings = load_user_bindings(target)
    assert bindings == list(DEFAULT_BINDINGS)
    assert any("Invalid user keybindings" in r.message for r in caplog.records)


def test_unrecognized_version_falls_back_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Forward-compat per A2 — future ``version: 99`` configs warn-and-fallback,
    not hard-reject."""

    target = tmp_path / "bindings.json"
    target.write_text(
        json.dumps(
            {
                "version": 99,
                "bindings": [{"action": "x", "keys": ["k"]}],
            }
        )
    )
    with caplog.at_level(logging.WARNING, logger="src.tui.keybindings_loader"):
        bindings = load_user_bindings(target)
    assert bindings == list(DEFAULT_BINDINGS)
    assert any("unrecognized version" in r.message for r in caplog.records)


def test_round_trip_user_override() -> None:
    """Round-trip: user-config overrides one default; on load the override is
    present AND defaults for unconfigured actions still fire."""

    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as tmp:
        json.dump(
            {
                "version": 1,
                "bindings": [
                    {"action": "transcript.top", "keys": ["ctrl+home"]},
                    {"action": "transcript.search.open", "keys": ["ctrl+f"]},
                ],
            },
            tmp,
        )
        path = Path(tmp.name)
    try:
        bindings = load_user_bindings(path)
        action_to_keys = {e.action: e.keys for e in bindings}
        # Override applied:
        assert action_to_keys["transcript.top"] == ("ctrl+home",)
        # Novel user action appended:
        assert action_to_keys["transcript.search.open"] == ("ctrl+f",)
        # Other defaults still present:
        assert action_to_keys["transcript.bottom"] == ("G",)
    finally:
        path.unlink(missing_ok=True)


def test_default_path_resolves_to_user_home() -> None:
    """Smoke check that the constant resolves under the user's home dir."""

    from src.tui.keybindings_loader import _default_user_path

    p = _default_user_path()
    assert str(p).endswith("/.claude/keybindings.json")


def test_supported_versions_includes_1() -> None:
    assert 1 in KEY_VERSIONS_RECOGNIZED


def test_unreadable_file_falls_back_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Permission/IO errors on read fall back rather than crashing."""

    target = tmp_path / "bindings.json"
    target.write_text("{}")  # exists, but we'll make read_text fail.
    real_read_text = Path.read_text

    def explode(self: Path, *args, **kwargs):
        if self == target:
            raise PermissionError("simulated EACCES")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", explode)
    with caplog.at_level(logging.WARNING, logger="src.tui.keybindings_loader"):
        bindings = load_user_bindings(target)
    assert bindings == list(DEFAULT_BINDINGS)
    assert any("Could not read" in r.message for r in caplog.records)


def test_default_bindings_cover_chord_tracker_actions() -> None:
    """The minimum default set must cover every action the legacy
    ``ChordTracker`` exposed — otherwise WI-2.4 would silently drop some."""

    from src.tui.keybindings import default_bindings

    legacy_actions = {b.action for b in default_bindings()}
    new_actions = {e.action for e in DEFAULT_BINDINGS}
    # The new defaults should be a (possibly extended) superset of the
    # legacy chord-tracker action names.
    missing = legacy_actions - new_actions
    assert not missing, (
        f"DEFAULT_BINDINGS dropped legacy actions: {missing}. "
        f"Update DEFAULT_BINDINGS or rename the legacy actions consciously."
    )

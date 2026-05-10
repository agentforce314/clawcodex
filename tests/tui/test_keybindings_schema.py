"""Tests for ``src.tui.keybindings_schema`` (WI-2.1)."""

from __future__ import annotations

import pytest

from src.tui.keybindings_schema import (
    KeybindingEntry,
    KeybindingValidationError,
    SUPPORTED_VERSIONS,
    parse_key_sequence,
    validate_keybindings,
)


# ------------------------------------------------------------------
# parse_key_sequence
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("k", ["k"]),
        ("ctrl+k", ["ctrl+k"]),
        ("ctrl+shift+k", ["ctrl+shift+k"]),
        ("ctrl-shift-k", ["ctrl+shift+k"]),
        ("g g", ["g", "g"]),
        ("ctrl+w v", ["ctrl+w", "v"]),
        ("ctrl+space", ["ctrl+space"]),
        ("cmd+c", ["cmd+c"]),
        # Whitespace tolerance.
        ("  ctrl+a  ", ["ctrl+a"]),
        ("a  b", ["a", "b"]),
    ],
)
def test_parse_key_sequence_valid(spec: str, expected: list[str]) -> None:
    assert parse_key_sequence(spec) == expected


@pytest.mark.parametrize("spec", ["", "   ", "ctrl++k", "ctrl+", "+k", "a +b"])
def test_parse_key_sequence_rejects_malformed(spec: str) -> None:
    with pytest.raises(KeybindingValidationError):
        parse_key_sequence(spec)


# ------------------------------------------------------------------
# KeybindingEntry construction
# ------------------------------------------------------------------


def test_entry_requires_non_empty_action() -> None:
    with pytest.raises(KeybindingValidationError):
        KeybindingEntry(action="", keys=("k",))


def test_entry_requires_at_least_one_key() -> None:
    with pytest.raises(KeybindingValidationError):
        KeybindingEntry(action="foo", keys=())


def test_entry_rejects_empty_key_token() -> None:
    with pytest.raises(KeybindingValidationError):
        KeybindingEntry(action="foo", keys=("",))


def test_entry_is_frozen_and_hashable() -> None:
    entry = KeybindingEntry(action="a", keys=("k",))
    with pytest.raises(Exception):
        entry.action = "b"  # type: ignore[misc]
    # Hashable so resolvers can index by entry.
    assert hash(entry) == hash(KeybindingEntry(action="a", keys=("k",)))


def test_description_does_not_affect_equality_or_hash() -> None:
    """Equality keys on action+keys+when. Description is metadata only."""

    a = KeybindingEntry(action="x", keys=("k",), description="one")
    b = KeybindingEntry(action="x", keys=("k",), description="two")
    assert a == b
    assert hash(a) == hash(b)


# ------------------------------------------------------------------
# validate_keybindings — top-level shape
# ------------------------------------------------------------------


def test_minimal_valid_config_round_trips() -> None:
    data = {
        "version": 1,
        "bindings": [
            {"action": "transcript.scroll_top", "keys": ["g", "g"]},
        ],
    }
    bindings = validate_keybindings(data)
    assert len(bindings) == 1
    assert bindings[0] == KeybindingEntry(
        action="transcript.scroll_top",
        keys=("g", "g"),
    )


def test_with_clause_propagates() -> None:
    data = {
        "version": 1,
        "bindings": [
            {
                "action": "transcript.scroll_top",
                "keys": ["g", "g"],
                "when": "transcript.focused",
            },
        ],
    }
    bindings = validate_keybindings(data)
    assert bindings[0].when == "transcript.focused"


def test_description_propagates() -> None:
    data = {
        "version": 1,
        "bindings": [
            {
                "action": "x",
                "keys": ["k"],
                "description": "Do the thing",
            },
        ],
    }
    bindings = validate_keybindings(data)
    assert bindings[0].description == "Do the thing"


def test_top_level_must_be_object() -> None:
    with pytest.raises(KeybindingValidationError, match="top-level"):
        validate_keybindings([])


def test_missing_version_rejected() -> None:
    with pytest.raises(KeybindingValidationError, match="version"):
        validate_keybindings({"bindings": []})


def test_missing_bindings_rejected() -> None:
    with pytest.raises(KeybindingValidationError, match="bindings"):
        validate_keybindings({"version": 1})


def test_version_must_be_integer() -> None:
    with pytest.raises(KeybindingValidationError, match="integer"):
        validate_keybindings({"version": "1", "bindings": []})


def test_version_bool_is_rejected() -> None:
    """``True``/``False`` are ints in Python; reject them explicitly."""

    with pytest.raises(KeybindingValidationError, match="integer"):
        validate_keybindings({"version": True, "bindings": []})


def test_unsupported_version_rejected() -> None:
    """Per A2 — schema rejects unknown versions; loader policy decides what to do.

    The loader (WI-2.2) is responsible for the deprecation-warning fallback;
    the schema itself is strict.
    """

    with pytest.raises(KeybindingValidationError, match="unsupported version"):
        validate_keybindings({"version": 99, "bindings": []})


def test_supported_versions_includes_1() -> None:
    assert 1 in SUPPORTED_VERSIONS


def test_unknown_top_level_field_rejected() -> None:
    with pytest.raises(KeybindingValidationError, match="unknown top-level"):
        validate_keybindings({"version": 1, "bindings": [], "extra": True})


def test_bindings_must_be_array() -> None:
    with pytest.raises(KeybindingValidationError, match="bindings"):
        validate_keybindings({"version": 1, "bindings": "nope"})


# ------------------------------------------------------------------
# validate_keybindings — per-entry validation
# ------------------------------------------------------------------


def test_entry_must_be_object() -> None:
    with pytest.raises(KeybindingValidationError, match=r"bindings\[0\]"):
        validate_keybindings({"version": 1, "bindings": [42]})


def test_entry_missing_action() -> None:
    with pytest.raises(KeybindingValidationError, match="action"):
        validate_keybindings({"version": 1, "bindings": [{"keys": ["k"]}]})


def test_entry_missing_keys() -> None:
    with pytest.raises(KeybindingValidationError, match="keys"):
        validate_keybindings({"version": 1, "bindings": [{"action": "x"}]})


def test_entry_empty_keys_array_rejected() -> None:
    with pytest.raises(KeybindingValidationError, match="keys"):
        validate_keybindings(
            {"version": 1, "bindings": [{"action": "x", "keys": []}]}
        )


def test_entry_non_string_key_token_rejected() -> None:
    with pytest.raises(KeybindingValidationError, match="keys"):
        validate_keybindings(
            {"version": 1, "bindings": [{"action": "x", "keys": ["a", 7]}]}
        )


def test_entry_empty_when_rejected() -> None:
    with pytest.raises(KeybindingValidationError, match="when"):
        validate_keybindings(
            {
                "version": 1,
                "bindings": [{"action": "x", "keys": ["k"], "when": ""}],
            }
        )


def test_entry_unknown_field_rejected() -> None:
    """Typo prevention — silently accepting unknown fields lets users
    misconfigure for hours wondering why a binding doesn't fire."""

    with pytest.raises(KeybindingValidationError, match="unknown field"):
        validate_keybindings(
            {
                "version": 1,
                "bindings": [{"action": "x", "keys": ["k"], "active": True}],
            }
        )


def test_chord_string_flattens_into_chord_array() -> None:
    """``{"keys": ["g g"]}`` parses to a 2-position chord, NOT a literal token."""

    data = {
        "version": 1,
        "bindings": [{"action": "transcript.top", "keys": ["g g"]}],
    }
    bindings = validate_keybindings(data)
    assert bindings[0].keys == ("g", "g")


def test_array_form_and_string_form_are_equivalent() -> None:
    """Either ``{"keys": ["g", "g"]}`` or ``{"keys": ["g g"]}`` works."""

    array_form = validate_keybindings(
        {
            "version": 1,
            "bindings": [{"action": "x", "keys": ["g", "g"]}],
        }
    )
    string_form = validate_keybindings(
        {
            "version": 1,
            "bindings": [{"action": "x", "keys": ["g g"]}],
        }
    )
    assert array_form[0].keys == string_form[0].keys


def test_modifier_join_in_keys_array() -> None:
    """``{"keys": ["ctrl+shift+k"]}`` is a single-position chord with modifiers."""

    data = {
        "version": 1,
        "bindings": [{"action": "x", "keys": ["ctrl+shift+k"]}],
    }
    bindings = validate_keybindings(data)
    assert bindings[0].keys == ("ctrl+shift+k",)


def test_mixed_chord_and_modifier_in_one_string() -> None:
    """``{"keys": ["ctrl+w v"]}`` is a 2-position chord whose first position has modifiers."""

    data = {
        "version": 1,
        "bindings": [{"action": "x", "keys": ["ctrl+w v"]}],
    }
    bindings = validate_keybindings(data)
    assert bindings[0].keys == ("ctrl+w", "v")


def test_malformed_key_token_inside_array_propagates_error() -> None:
    """``{"keys": ["ctrl++k"]}`` should fail validation, citing the offending index."""

    with pytest.raises(KeybindingValidationError, match=r"keys\[0\]"):
        validate_keybindings(
            {
                "version": 1,
                "bindings": [{"action": "x", "keys": ["ctrl++k"]}],
            }
        )


def test_multiple_bindings_round_trip() -> None:
    data = {
        "version": 1,
        "bindings": [
            {"action": "a1", "keys": ["k"]},
            {"action": "a2", "keys": ["g", "g"], "when": "transcript.focused"},
            {"action": "a3", "keys": ["ctrl+f"], "description": "search"},
        ],
    }
    bindings = validate_keybindings(data)
    assert len(bindings) == 3
    assert bindings[0].action == "a1"
    assert bindings[1].keys == ("g", "g")
    assert bindings[1].when == "transcript.focused"
    assert bindings[2].description == "search"

"""JSON schema + parser for ``~/.claude/keybindings.json``.

Mirrors the type + validation surface of
``typescript/src/keybindings/{schema.ts, parser.ts, types.ts, validate.ts}``
adapted for Python's ``jsonschema``-free environment (we hand-validate to
avoid pulling another dep). The on-disk shape is::

    {
      "version": 1,
      "bindings": [
        {"action": "transcript.scroll_top", "keys": ["ctrl+home"]},
        {"action": "transcript.search.open", "keys": ["ctrl+f"]},
        {"action": "transcript.scroll_top", "keys": ["g", "g"], "when": "transcript.focused"}
      ]
    }

Per refactoring-plan A2 — versioning policy is *enum-based* (NOT ``const``).
Future ``version: 2`` configs are recognized by the loader and dispatched
through per-version field handling; configs with unknown versions fall back
to defaults with a deprecation warning rather than hard rejection. This
keeps users who run ahead of the codebase from getting locked out.

Public surface:

* :class:`KeybindingEntry` — a single typed binding rule
* :func:`parse_key_sequence` — splits ``"ctrl+shift+k"`` → ``["ctrl", "shift", "k"]``
* :func:`validate_keybindings` — typed-record loader + validator
* :data:`SUPPORTED_VERSIONS` — the enum of accepted ``version`` integers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final


SUPPORTED_VERSIONS: Final[tuple[int, ...]] = (1,)


class KeybindingValidationError(ValueError):
    """Raised when a keybindings config is structurally wrong.

    The loader (WI-2.2) catches this and falls back to defaults with a
    user-readable error; tests assert on it directly.
    """


@dataclass(frozen=True)
class KeybindingEntry:
    """One binding rule.

    Mirrors the chapter's ``KeybindingEntry`` type with three fields:
    ``action`` (action id like ``"transcript.scroll_top"``), ``keys``
    (a sequence of one or more key tokens — single-key bindings are a
    one-element sequence; chords have two or more), and an optional
    ``when`` context predicate (only fire if the named context is in
    the active set).

    Frozen + hashable so resolvers can use these as dict keys when
    pre-computing context indices.
    """

    action: str
    keys: tuple[str, ...]
    when: str | None = None
    description: str = field(default="", compare=False, hash=False)

    def __post_init__(self) -> None:
        if not self.action:
            raise KeybindingValidationError("action must be a non-empty string")
        if not self.keys:
            raise KeybindingValidationError(
                f"keys must contain at least one token (action={self.action!r})"
            )
        for token in self.keys:
            if not isinstance(token, str) or not token:
                raise KeybindingValidationError(
                    f"each key token must be a non-empty string "
                    f"(action={self.action!r}, got {token!r})"
                )


def parse_key_sequence(s: str) -> list[str]:
    """Split a hyphen/plus-separated key spec into a list of tokens.

    Mirrors ``typescript/src/keybindings/parser.ts``. Examples::

        parse_key_sequence("ctrl+shift+k") -> ["ctrl", "shift", "k"]
        parse_key_sequence("ctrl-shift-k") -> ["ctrl", "shift", "k"]
        parse_key_sequence("g g")          -> ["g", "g"]   (chord — split on space)
        parse_key_sequence("ctrl+space")   -> ["ctrl", "space"]

    Whitespace separates *chord positions*; ``+`` / ``-`` join modifiers
    within a single position. Mixed inputs (``"ctrl+w v"``) yield two
    chord positions where the first is the modifier-laden ``"ctrl+w"``.
    """

    if not s or not s.strip():
        raise KeybindingValidationError("empty key sequence")
    chord = []
    for position in s.strip().split():
        # Modifier joins: accept both "+" and "-" as separators.
        normalized = position.replace("-", "+")
        # Validate every modifier token is non-empty (catches "ctrl++k").
        parts = normalized.split("+")
        if any(not p for p in parts):
            raise KeybindingValidationError(
                f"empty token in key sequence: {position!r}"
            )
        # Re-join with "+" so the canonical form is stable.
        chord.append("+".join(parts))
    return chord


def validate_keybindings(data: Any) -> list[KeybindingEntry]:
    """Validate a parsed JSON object and return the typed bindings list.

    Strict mode: every error condition raises :class:`KeybindingValidationError`
    with a message naming the offending field. The loader (WI-2.2) catches
    these and decides the fallback behavior.

    Forward-compat note (refactoring-plan A2): ``version`` accepted values
    live in :data:`SUPPORTED_VERSIONS` — when a future revision adds a v2
    field set, append ``2`` to ``SUPPORTED_VERSIONS`` and dispatch on the
    version inside this function. Hard-rejecting unknown versions belongs
    in the loader, not here, so future configs can be partially-loaded
    if useful.
    """

    if not isinstance(data, dict):
        raise KeybindingValidationError(
            f"top-level config must be an object; got {type(data).__name__}"
        )
    if "version" not in data:
        raise KeybindingValidationError("missing required field 'version'")
    version = data["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise KeybindingValidationError(
            f"'version' must be an integer; got {type(version).__name__}"
        )
    if version not in SUPPORTED_VERSIONS:
        raise KeybindingValidationError(
            f"unsupported version {version}; supported: {SUPPORTED_VERSIONS}"
        )
    if "bindings" not in data:
        raise KeybindingValidationError("missing required field 'bindings'")
    raw_bindings = data["bindings"]
    if not isinstance(raw_bindings, list):
        raise KeybindingValidationError(
            f"'bindings' must be an array; got {type(raw_bindings).__name__}"
        )

    # Reject unknown top-level fields to catch typos. Forward-compat fields
    # for future versions land via SUPPORTED_VERSIONS dispatch above, not
    # via silent acceptance here.
    extra = set(data) - {"version", "bindings"}
    if extra:
        raise KeybindingValidationError(
            f"unknown top-level field(s): {sorted(extra)}"
        )

    bindings: list[KeybindingEntry] = []
    for index, entry in enumerate(raw_bindings):
        bindings.append(_validate_entry(entry, index))
    return bindings


def _validate_entry(entry: Any, index: int) -> KeybindingEntry:
    if not isinstance(entry, dict):
        raise KeybindingValidationError(
            f"bindings[{index}] must be an object; got {type(entry).__name__}"
        )
    if "action" not in entry:
        raise KeybindingValidationError(f"bindings[{index}]: missing 'action'")
    action = entry["action"]
    if not isinstance(action, str) or not action:
        raise KeybindingValidationError(
            f"bindings[{index}]: 'action' must be a non-empty string"
        )
    if "keys" not in entry:
        raise KeybindingValidationError(f"bindings[{index}]: missing 'keys'")
    raw_keys = entry["keys"]
    if not isinstance(raw_keys, list) or not raw_keys:
        raise KeybindingValidationError(
            f"bindings[{index}]: 'keys' must be a non-empty array"
        )

    # Each ``keys[i]`` element may be either a single chord position
    # (``"ctrl+f"``, ``"k"``) OR a whitespace-separated chord string
    # (``"g g"``, ``"ctrl+w v"``). ``parse_key_sequence`` handles both;
    # we flatten the per-element parses into the final chord. This
    # closes the WI-2.1 gap where ``{"keys": ["g g"]}`` (one element
    # with a chord string inside) silently produced a single-token
    # binding.
    chord: list[str] = []
    for k_idx, token in enumerate(raw_keys):
        if not isinstance(token, str) or not token:
            raise KeybindingValidationError(
                f"bindings[{index}].keys[{k_idx}]: must be a non-empty string"
            )
        try:
            chord.extend(parse_key_sequence(token))
        except KeybindingValidationError as exc:
            raise KeybindingValidationError(
                f"bindings[{index}].keys[{k_idx}]: {exc}"
            ) from None

    when = entry.get("when")
    if when is not None and (not isinstance(when, str) or not when):
        raise KeybindingValidationError(
            f"bindings[{index}]: 'when' must be a non-empty string when present"
        )
    description = entry.get("description", "")
    if description is not None and not isinstance(description, str):
        raise KeybindingValidationError(
            f"bindings[{index}]: 'description' must be a string when present"
        )

    extra = set(entry) - {"action", "keys", "when", "description"}
    if extra:
        raise KeybindingValidationError(
            f"bindings[{index}]: unknown field(s) {sorted(extra)}"
        )

    return KeybindingEntry(
        action=action,
        keys=tuple(chord),
        when=when,
        description=description or "",
    )


__all__ = [
    "KeybindingEntry",
    "KeybindingValidationError",
    "SUPPORTED_VERSIONS",
    "parse_key_sequence",
    "validate_keybindings",
]

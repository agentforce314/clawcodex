"""Load + merge keybindings from ``~/.claude/keybindings.json``.

Mirrors ``typescript/src/keybindings/loadUserBindings.ts`` (472 lines in
the TS reference) at the fidelity Python needs — load, validate, merge
with defaults, return a typed list. Per refactoring-plan A2 versioning
policy:

* Configs whose ``version`` is in :data:`KEY_VERSIONS_RECOGNIZED` load
  successfully (today only ``[1]``; future ``[1, 2]`` etc.).
* Configs with structural errors (bad shape, unknown field, malformed
  key tokens) fall back to defaults with a logged warning. Hard-rejection
  would lock users out of their TUI for a typo; deprecation-warning is
  the right user experience.
* Configs with completely unknown ``version`` (e.g. user runs ahead of
  the codebase) also fall back to defaults with a deprecation warning.
* Missing config file (the common case for first-run users) silently
  returns defaults — not a warning.

Public surface:

* :data:`DEFAULT_BINDINGS` — the minimum default set the TUI ships with.
* :func:`load_user_bindings` — the high-level "give me the active list".
* :func:`merge_bindings` — pure helper for testing the merge semantics.
* :func:`_default_user_path` — resolves ``~/.claude/keybindings.json``
  (lazy so test env overrides of ``$HOME`` take effect).

Note: the default set here is *minimum* — only the actions ``ChordTracker``
exposed historically plus a few new ones the refactor plan unlocks. The
full ~341-line chapter default set will fill in across later phases as
each subsystem (search, vim, etc.) lands.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Final

from .keybindings_schema import (
    KeybindingEntry,
    KeybindingValidationError,
    SUPPORTED_VERSIONS,
    validate_keybindings,
)


_logger = logging.getLogger(__name__)


# Versions the loader can *read*. Today this matches SUPPORTED_VERSIONS
# from the schema; future revisions can decide to keep reading older
# versions for one or two release cycles after the schema rejects them.
KEY_VERSIONS_RECOGNIZED: Final[tuple[int, ...]] = SUPPORTED_VERSIONS


# Resolve the default path lazily so test environments can override
# ``HOME`` (``$CLAUDE_HOME`` precedence isn't a chapter-13 concern; if
# users want a custom location they can pass an explicit ``path``).
#
# Critic-flagged: the previous module-level ``DEFAULT_USER_PATH``
# constant captured ``HOME`` at import time, so tests that monkeypatched
# ``HOME`` after import never saw the override unless they called the
# function directly. Removed in favor of always-fresh lookup via
# :func:`_default_user_path`.
def _default_user_path() -> Path:
    return Path(os.path.expanduser("~/.claude/keybindings.json"))


# Minimum defaults — the seven historical chord bindings + a few new
# actions later phases will use. Names match the chord-tracker action
# strings so WI-2.4's ChordTracker refactor can read directly from this.
DEFAULT_BINDINGS: Final[tuple[KeybindingEntry, ...]] = (
    # Action names preserved from legacy ``src/tui/keybindings.py`` so the
    # WI-2.4 ChordTracker refactor is a drop-in. If the team wants to
    # rename to ``transcript.scroll_top`` etc. for chapter parity, do it as
    # a separate ticket so the rename's blast radius (every consumer that
    # listens for an action name) lands in one focused PR.
    KeybindingEntry(
        action="transcript.top",
        keys=("g", "g"),
        description="Jump to oldest message",
    ),
    KeybindingEntry(
        action="transcript.bottom",
        keys=("G",),
        description="Jump to latest message",
    ),
    KeybindingEntry(
        action="transcript.prev-change",
        keys=("[", "c"),
        description="Previous tool result",
    ),
    KeybindingEntry(
        action="transcript.next-change",
        keys=("]", "c"),
        description="Next tool result",
    ),
    KeybindingEntry(
        action="transcript.prev-message",
        keys=("[", "m"),
        description="Previous message",
    ),
    KeybindingEntry(
        action="transcript.next-message",
        keys=("]", "m"),
        description="Next message",
    ),
    KeybindingEntry(
        action="layout.toggle-overlay",
        keys=("ctrl+w", "v"),
        description="Toggle overlay",
    ),
    # Phase-2 WI-2.6: real production binding — used by REPLScreen.
    # Action name namespaced under ``transcript.`` to match the existing
    # transcript-related actions; key matches the current Textual
    # ``BINDINGS`` declaration so user-visible behavior stays put when the
    # full BINDINGS migration lands later.
    KeybindingEntry(
        action="transcript.clear",
        keys=("ctrl+l",),
        description="Clear transcript",
    ),
)


def merge_bindings(
    defaults: list[KeybindingEntry] | tuple[KeybindingEntry, ...],
    user: list[KeybindingEntry] | tuple[KeybindingEntry, ...],
) -> list[KeybindingEntry]:
    """Return defaults merged with user overrides — last-write-wins per ``action``.

    Semantics (mirrors TS ``loadUserBindings`` merge):

    * If a user entry's ``action`` matches a default entry's ``action``,
      **the user entry replaces the default** for that action — including
      its ``keys`` AND its ``when`` clause. This is the override pattern.
    * User entries with novel actions are *appended* (additive).
    * The merge is keyed on ``action`` only — not on ``(action, when)``.
      Rationale: the chapter's TS code keys on action; we match that. A
      user wanting context-specific overrides authors the same action
      twice in their config (the schema accepts it; resolver handles
      most-specific-context-wins downstream).

    Pure function — no I/O, no logging. Useful for unit tests and for
    callers that already have validated lists.
    """

    user_actions: dict[str, list[KeybindingEntry]] = {}
    for entry in user:
        user_actions.setdefault(entry.action, []).append(entry)

    out: list[KeybindingEntry] = []
    overridden: set[str] = set()
    for entry in defaults:
        if entry.action in user_actions:
            # First time we see an overridden action: emit ALL user
            # entries for it (preserves the "user can have multiple
            # context-scoped versions of the same action" case).
            if entry.action not in overridden:
                out.extend(user_actions[entry.action])
                overridden.add(entry.action)
            # Skip the default — user wins.
            continue
        out.append(entry)

    # Append any user entries whose actions did not match a default.
    # Pre-compute the default-action set once instead of rebuilding it
    # per user entry (Critic-flagged perf nit).
    default_actions = frozenset(d.action for d in defaults)
    for entry in user:
        if entry.action not in default_actions:
            out.append(entry)

    return out


def load_user_bindings(path: Path | str | None = None) -> list[KeybindingEntry]:
    """Load + merge user keybindings with defaults.

    Args:
        path: Override the default ``~/.claude/keybindings.json`` lookup.
            When ``None`` the module-resolved default is used.

    Returns:
        Merged list of :class:`KeybindingEntry` records — guaranteed to
        cover every default action; user overrides land first per
        :func:`merge_bindings` semantics.

    Logs (at WARNING) on every recoverable failure: missing file is
    silent (DEBUG); malformed JSON, schema errors, unknown version,
    permission errors all warn-and-fallback.
    """

    target = Path(path) if path is not None else _default_user_path()
    if not target.exists():
        _logger.debug("No user keybindings at %s; using defaults", target)
        return list(DEFAULT_BINDINGS)

    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _logger.warning(
            "Could not read user keybindings at %s (%s); using defaults",
            target,
            exc,
        )
        return list(DEFAULT_BINDINGS)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _logger.warning(
            "Malformed JSON in user keybindings at %s (%s); using defaults",
            target,
            exc,
        )
        return list(DEFAULT_BINDINGS)

    # Forward-compat per A2: if the file's ``version`` is outside the set
    # we recognize, log and fall back rather than hard-rejecting.
    version = data.get("version") if isinstance(data, dict) else None
    if isinstance(version, int) and not isinstance(version, bool):
        if version not in KEY_VERSIONS_RECOGNIZED:
            _logger.warning(
                "User keybindings at %s declare unrecognized version %d; "
                "supported versions: %s. Falling back to defaults; "
                "consider upgrading or pinning your config.",
                target,
                version,
                KEY_VERSIONS_RECOGNIZED,
            )
            return list(DEFAULT_BINDINGS)

    try:
        user_entries = validate_keybindings(data)
    except KeybindingValidationError as exc:
        _logger.warning(
            "Invalid user keybindings at %s (%s); using defaults",
            target,
            exc,
        )
        return list(DEFAULT_BINDINGS)

    return merge_bindings(DEFAULT_BINDINGS, user_entries)


__all__ = [
    "DEFAULT_BINDINGS",
    "KEY_VERSIONS_RECOGNIZED",
    "load_user_bindings",
    "merge_bindings",
]

"""End-to-end pipeline tests for Phase 2 keybindings (WI-2.6).

The acceptance criterion in the refactoring plan §6 G2:

  Round-trip test: user-config overrides at least one default binding;
  on TUI restart the override is loaded and the corresponding action
  fires; default bindings for unconfigured actions still fire.

These tests exercise the full pipeline — schema parse → loader merge →
chord tracker resolution → registry dispatch — without depending on
Textual's render harness. They're the proof of concept that all five
Phase-2 modules compose.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.tui.keybindings_dispatcher import KeybindingDispatcher


# ------------------------------------------------------------------
# Default bindings dispatch
# ------------------------------------------------------------------


def test_default_chord_fires_registered_handler() -> None:
    """`g g` is a default binding for ``transcript.top``; a registered
    handler for that action should fire."""

    dispatcher = KeybindingDispatcher.from_defaults()
    fired = []
    dispatcher.register("transcript.top", lambda: fired.append("top"))

    # First g: chord pending, no handler fires.
    assert dispatcher.feed_key("g", now=0.0) is None
    assert fired == []

    # Second g: chord completes, action fires.
    assert dispatcher.feed_key("g", now=0.1) == "transcript.top"
    assert fired == ["top"]


def test_unhandled_action_returns_false_silently() -> None:
    """A default binding fires but no handler is registered → no crash."""

    dispatcher = KeybindingDispatcher.from_defaults()
    # ``G`` resolves to ``transcript.bottom`` per defaults, but no handler.
    action = dispatcher.feed_key("G", now=0.0)
    assert action == "transcript.bottom"  # Tracker fired; nobody listened.


# ------------------------------------------------------------------
# User config override
# ------------------------------------------------------------------


@pytest.fixture
def user_config(tmp_path: Path) -> Path:
    """Write a user keybindings config that overrides ``transcript.top``."""

    target = tmp_path / "keybindings.json"
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": [
                    {"action": "transcript.top", "keys": ["ctrl+home"]},
                    {"action": "transcript.search.open", "keys": ["ctrl+f"]},
                ],
            }
        )
    )
    return target


def test_user_override_changes_default_binding(user_config: Path) -> None:
    """The round-trip case: user config remaps ``g g`` → ``ctrl+home``.

    After load, the new key dispatch fires the same action handler.
    """

    from src.tui.keybindings_loader import load_user_bindings

    dispatcher = KeybindingDispatcher(load_user_bindings(user_config))
    fired = []
    dispatcher.register("transcript.top", lambda: fired.append("top"))

    # Old binding should NO LONGER fire (default `g g` was overridden).
    assert dispatcher.feed_key("g", now=0.0) is None
    assert dispatcher.feed_key("g", now=0.1) is None  # No match anymore.
    assert fired == []

    # New binding fires the same handler.
    assert dispatcher.feed_key("ctrl+home", now=1.0) == "transcript.top"
    assert fired == ["top"]


def test_default_unconfigured_actions_still_fire(user_config: Path) -> None:
    """Round-trip part 2: defaults for actions NOT in the user config still work."""

    from src.tui.keybindings_loader import load_user_bindings

    dispatcher = KeybindingDispatcher(load_user_bindings(user_config))
    fired = []
    dispatcher.register("transcript.bottom", lambda: fired.append("bottom"))

    # The user config didn't touch transcript.bottom → default `G` still fires.
    assert dispatcher.feed_key("G", now=0.0) == "transcript.bottom"
    assert fired == ["bottom"]


def test_user_added_action_dispatches_to_registered_handler(
    user_config: Path,
) -> None:
    """Round-trip part 3: a novel user action (transcript.search.open) lands."""

    from src.tui.keybindings_loader import load_user_bindings

    dispatcher = KeybindingDispatcher(load_user_bindings(user_config))
    fired = []
    dispatcher.register("transcript.search.open", lambda: fired.append("search"))

    assert dispatcher.feed_key("ctrl+f", now=0.0) == "transcript.search.open"
    assert fired == ["search"]


# ------------------------------------------------------------------
# Context-scoped handlers
# ------------------------------------------------------------------


def test_scoped_handler_only_fires_when_context_active() -> None:
    """A context-scoped handler doesn't fire until ``add_context`` activates it."""

    dispatcher = KeybindingDispatcher.from_defaults()
    fired = []
    dispatcher.register(
        "transcript.bottom",
        lambda: fired.append("scoped"),
        context_name="transcript.focused",
    )

    # Context inactive: handler does NOT fire (no fallback, no global handler).
    dispatcher.feed_key("G", now=0.0)
    assert fired == []

    # Activate the context: same key fires the handler.
    dispatcher.add_context("transcript.focused")
    dispatcher.feed_key("G", now=1.0)
    assert fired == ["scoped"]


def test_scope_deactivation_via_remove_context() -> None:
    dispatcher = KeybindingDispatcher.from_defaults()
    fired = []
    dispatcher.register(
        "transcript.top",
        lambda: fired.append("scoped"),
        context_name="transcript.focused",
    )
    dispatcher.add_context("transcript.focused")
    dispatcher.feed_key("g", now=0.0)
    dispatcher.feed_key("g", now=0.1)
    assert fired == ["scoped"]

    dispatcher.remove_context("transcript.focused")
    dispatcher.feed_key("g", now=1.0)
    dispatcher.feed_key("g", now=1.1)
    assert fired == ["scoped"]  # Still just the one fire.


# ------------------------------------------------------------------
# Unregister cleanup
# ------------------------------------------------------------------


def test_unregister_stops_handler() -> None:
    dispatcher = KeybindingDispatcher.from_defaults()
    fired = []
    unreg = dispatcher.register("transcript.top", lambda: fired.append("hit"))

    dispatcher.feed_key("g", now=0.0)
    dispatcher.feed_key("g", now=0.1)
    assert fired == ["hit"]

    unreg()
    dispatcher.feed_key("g", now=1.0)
    dispatcher.feed_key("g", now=1.1)
    assert fired == ["hit"]  # No further fires.


# ------------------------------------------------------------------
# Construct from user config (no explicit path)
# ------------------------------------------------------------------


def test_from_user_config_constructor_falls_back_to_defaults_on_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``from_user_config`` reads the default ``~/.claude/keybindings.json``;
    when absent, defaults stand in. This validates the constructor path."""

    # Redirect HOME so the loader probes a nonexistent path.
    monkeypatch.setenv("HOME", str(tmp_path))
    dispatcher = KeybindingDispatcher.from_user_config()
    fired = []
    dispatcher.register("transcript.top", lambda: fired.append("hit"))
    dispatcher.feed_key("g", now=0.0)
    dispatcher.feed_key("g", now=0.1)
    assert fired == ["hit"]


# ------------------------------------------------------------------
# WI-2.6 wiring proof: REPLScreen consumes the dispatcher in production
# ------------------------------------------------------------------


def test_app_exposes_keybindings_dispatcher() -> None:
    """``ClawCodexTUI`` must instantiate a real ``KeybindingDispatcher`` on
    init so screens can register handlers via ``self.app.keybindings``."""

    # Import lazily so test collection doesn't pay the ClawCodexTUI cost.
    import importlib

    app_module = importlib.import_module("src.tui.app")
    # Verify the constructor sets ``self.keybindings`` to a dispatcher.
    # We don't fully construct the App (it requires a provider with a real
    # API key) — we just check the source as a proxy.
    src = (Path(app_module.__file__)).read_text()
    assert "KeybindingDispatcher.from_user_config" in src, (
        "ClawCodexTUI.__init__ must instantiate a KeybindingDispatcher; "
        "the WI-2.6 wiring requires a production callsite."
    )


def test_repl_screen_registers_clear_transcript_handler() -> None:
    """When ``REPLScreen.on_mount`` fires, it must register a handler for
    the ``transcript.clear`` action via the app's dispatcher.

    Static check on the source — full Textual integration tests live
    under ``test_app_pilot.py`` and friends; we just need to verify the
    wiring is present so a future refactor doesn't silently delete it.
    """

    import importlib

    repl_module = importlib.import_module("src.tui.screens.repl")
    src = (Path(repl_module.__file__)).read_text()
    assert "transcript.clear" in src
    assert "dispatcher.register" in src
    assert "_kb_unregister" in src

"""Tests for the ``/theme`` command (Phase 5 — port of TS local-jsx).

Ports the behavior of ``typescript/src/commands/theme/`` (``theme.tsx`` +
``index.ts``) onto the interactive-command bridge, mirroring the ``/export`` test
layout (``tests/test_export_command.py``). ``/theme`` is the **inverse** of
``/export`` at the TUI dispatch layer: ``/export`` proves it *falls through* so the
registry arm runs, while ``/theme`` must keep the TUI **intercepting** (``handled=
True``, ``open_dialog="theme"``) so the rich live-preview ``ThemePickerScreen`` is
preserved. The ``ThemeCommand`` is therefore exercised only on registry-consulting
surfaces (REPL/SDK/listings).

Sections:
  * A — metadata + registration (INTERACTIVE, name, verbatim TS description, no hint).
  * B — bridge-safety **by type** + the TUI dispatch **inversion** (the anti-regression
    assertion: ``/theme`` stays intercepted, NOT fall-through).
  * C — picker happy path: success → ``display="user"`` (TS no-options ``onDone`` →
    model-visible ``createUserMessage``), the pick is persisted, and ``select`` is
    seeded from config with the full theme list.
  * D — cancel path: ``"Theme picker dismissed"`` / ``display="system"`` (NOT ``skip``),
    config unchanged.
  * E — null surface: ``select`` raises → engine returns a clean error (no headless
    keystone); args are ignored (TS ignores ``_context``).
  * F — options shape: values == ``list_theme_names()``; the current option carries
    ``description="current"``; labels are the raw theme names.
  * G — D2 wiring: ``_open_theme_picker`` passes ``on_persist=set_theme`` to the screen.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import src.config as cfg
from src.command_system import (
    THEME_COMMAND,
    ThemeCommand,
    create_command_context,
    get_builtin_commands,
    get_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.types import (
    CommandType,
    InteractiveOutcome,
    InteractiveUnavailableError,
    NullUIHost,
)
from src.tui.theme import list_theme_names


# --------------------------------------------------------------------------- #
# Fakes / fixtures
# --------------------------------------------------------------------------- #
class FakeUIHost:
    """Scripted UI surface recording ``select`` calls. ``pick`` is returned by
    ``select`` (``None`` = cancel). ``prompt_text``/``display`` round out the
    Protocol (``/theme`` only uses ``select``)."""

    def __init__(self, *, pick: str | None = None, text: str | None = None) -> None:
        self._pick = pick
        self._text = text
        self.select_calls: list[dict] = []
        self.prompt_calls: list[dict] = []
        self.display_calls: list[tuple[str, str]] = []

    async def select(self, title, options, *, current=None):
        self.select_calls.append(
            {
                "title": title,
                "values": [o.value for o in options],
                "labels": [o.label for o in options],
                "descriptions": [o.description for o in options],
                "current": current,
            }
        )
        return self._pick

    async def prompt_text(self, title, *, default="", placeholder=None):
        self.prompt_calls.append(
            {"title": title, "default": default, "placeholder": placeholder}
        )
        return self._text

    async def display(self, title, body):
        self.display_calls.append((title, body))


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the global config at a tmp file and give the singleton a fresh manager
    rooted at a non-git tmp cwd, so tests never touch ``~/.clawcodex/config.json`` and
    project/local configs resolve empty (``_find_git_root(tmp_path)`` is ``None``).

    N2: the theme value is read via ``load_config()`` / ``ConfigManager`` (not the
    settings cache), so the only state to reset is the manager singleton + its backing
    file. ``monkeypatch`` auto-restores both attrs after the test.
    """
    monkeypatch.setattr(cfg, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "_default_manager", cfg.ConfigManager(cwd=tmp_path))
    return tmp_path


def _ctx(tmp_path: Path, *, ui=None):
    # /theme ignores the conversation entirely (picker-only), so we don't wire one.
    return create_command_context(workspace_root=tmp_path, cwd=tmp_path, ui=ui)


def _registry_with(*commands) -> CommandRegistry:
    reg = CommandRegistry()
    for c in commands:
        reg.register(c)
    return reg


def _persisted_theme(tmp_path: Path):
    """Read ``theme`` back through a FRESH manager (true on-disk round-trip)."""
    return cfg.ConfigManager(cwd=tmp_path).get("theme")


# --------------------------------------------------------------------------- #
# A. Metadata + registration
# --------------------------------------------------------------------------- #
def test_theme_registered_in_builtins_and_aggregator():
    assert "theme" in {c.name for c in get_builtin_commands()}
    assert "theme" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_theme_metadata_mirrors_ts():
    assert isinstance(THEME_COMMAND, ThemeCommand)
    assert THEME_COMMAND.name == "theme"
    # Verbatim from typescript/src/commands/theme/index.ts.
    assert THEME_COMMAND.description == "Change the theme"
    # TS sets no argumentHint (the command ignores args).
    assert THEME_COMMAND.argument_hint is None
    # local-jsx -> INTERACTIVE (so the remote/bridge gate blocks it by type).
    assert THEME_COMMAND.command_type == CommandType.INTERACTIVE
    # TS sets only type/name/description; everything else defaults.
    assert THEME_COMMAND.is_hidden is False
    assert THEME_COMMAND.disable_model_invocation is False
    assert THEME_COMMAND.user_invocable is True


# --------------------------------------------------------------------------- #
# B. Bridge-safety BY TYPE + TUI dispatch inversion
# --------------------------------------------------------------------------- #
def test_theme_blocked_from_bridge_by_type():
    # INTERACTIVE commands are never bridge-safe (mirrors TS local-jsx). Note this
    # is orthogonal to REMOTE_SAFE_COMMANDS (a name-based --remote filter that also
    # lists "theme"): the type gate still blocks the bridge.
    assert is_bridge_safe_command(THEME_COMMAND) is False


def test_dispatch_local_command_intercepts_theme():
    # THE INVERSION vs /export: the TUI's direct-dispatch table MUST claim /theme
    # (handled=True, open_dialog="theme") so the rich live-preview ThemePickerScreen
    # is preserved. Do NOT assert fall-through here — that was /export's rule. This
    # is the explicit anti-regression guarantee for the TUI dialog.
    from src.tui.commands import dispatch_local_command

    res = dispatch_local_command(
        "/theme", session=None, workspace_root=Path("."), tool_registry=None
    )
    assert res.handled is True
    assert res.open_dialog == "theme"


# --------------------------------------------------------------------------- #
# C. Picker happy path
# --------------------------------------------------------------------------- #
async def test_picker_happy_path_persists_and_reports(isolated_config):
    tmp_path = isolated_config
    ui = FakeUIHost(pick="light")
    outcome = await THEME_COMMAND.run("", _ctx(tmp_path, ui=ui))

    assert isinstance(outcome, InteractiveOutcome)
    assert outcome.message == "Theme set to light"
    # B1: success is model-visible in TS (no-options onDone -> createUserMessage),
    # so display="user", NOT "system".
    assert outcome.display == "user"
    assert outcome.should_query is False
    # The pick is persisted to the (isolated) global config.
    assert _persisted_theme(tmp_path) == "light"
    # select offered the full theme list and seeded current from config.
    assert ui.select_calls[0]["values"] == list_theme_names()


async def test_picker_seeds_current_from_persisted_theme(isolated_config):
    tmp_path = isolated_config
    cfg.set_theme("claude")  # pre-persist a non-default theme
    ui = FakeUIHost(pick="dark")
    await THEME_COMMAND.run("", _ctx(tmp_path, ui=ui))

    # current= reflects the persisted theme, so the picker pre-highlights it.
    assert ui.select_calls[0]["current"] == "claude"


async def test_picker_current_defaults_to_dark_when_unset(isolated_config):
    tmp_path = isolated_config  # no theme persisted
    ui = FakeUIHost(pick="light")
    await THEME_COMMAND.run("", _ctx(tmp_path, ui=ui))

    # _current_theme() falls back to "dark" (identical to app._resolve_theme_name()).
    assert ui.select_calls[0]["current"] == "dark"


# --------------------------------------------------------------------------- #
# D. Cancel path
# --------------------------------------------------------------------------- #
async def test_cancel_returns_system_dismissed_not_skip(isolated_config):
    tmp_path = isolated_config
    ui = FakeUIHost(pick=None)  # Esc
    outcome = await THEME_COMMAND.run("", _ctx(tmp_path, ui=ui))

    # Faithful to TS onDone("Theme picker dismissed", {display:"system"}) — a visible
    # system line, NOT skip (which would be display="skip").
    assert outcome.message == "Theme picker dismissed"
    assert outcome.display == "system"
    assert outcome.display != "skip"
    # Cancel does not persist.
    assert _persisted_theme(tmp_path) is None


# --------------------------------------------------------------------------- #
# E. Null surface (always needs a UI) + args ignored
# --------------------------------------------------------------------------- #
async def test_run_raises_on_null_surface(isolated_config):
    tmp_path = isolated_config
    # No args path => the picker is the only path => select must raise on NullUIHost
    # (proves there is no /export-style headless keystone).
    with pytest.raises(InteractiveUnavailableError):
        await THEME_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost()))
    assert _persisted_theme(tmp_path) is None


async def test_engine_errors_cleanly_on_null_surface(isolated_config):
    tmp_path = isolated_config
    reg = _registry_with(THEME_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    assert ctx.ui is None  # engine substitutes NullUIHost
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/theme")

    assert result.success is False
    assert result.error is not None
    # The clean "needs an interactive surface" message, not a stack trace.
    assert "interactive surface" in result.error


async def test_args_are_ignored(isolated_config):
    tmp_path = isolated_config
    # TS call(onDone, _context) ignores args: extra args don't shortcut the picker.
    ui = FakeUIHost(pick="dark")
    outcome = await THEME_COMMAND.run("light extra args", _ctx(tmp_path, ui=ui))

    assert outcome.message == "Theme set to dark"  # the PICK wins, not the args
    assert _persisted_theme(tmp_path) == "dark"
    assert len(ui.select_calls) == 1  # the picker still ran


# --------------------------------------------------------------------------- #
# F. Options shape
# --------------------------------------------------------------------------- #
async def test_options_shape_marks_current_and_uses_raw_labels(isolated_config):
    tmp_path = isolated_config
    cfg.set_theme("light")
    ui = FakeUIHost(pick="light")
    await THEME_COMMAND.run("", _ctx(tmp_path, ui=ui))

    call = ui.select_calls[0]
    names = list_theme_names()
    assert call["values"] == names
    # Labels are the raw theme names (mirrors ThemePickerScreen), not friendly labels.
    assert call["labels"] == names
    # Exactly the current option carries the "current" marker.
    for name, desc in zip(call["values"], call["descriptions"]):
        assert desc == ("current" if name == "light" else None)


# --------------------------------------------------------------------------- #
# G. D2 — _open_theme_picker wires on_persist=set_theme
# --------------------------------------------------------------------------- #
def test_open_theme_picker_wires_on_persist(monkeypatch):
    # Assert the wiring (not a live Textual render): _open_theme_picker must pass
    # on_persist=set_theme so the TUI persists like TS. The screen fires on_persist
    # only on selection, not on cancel (theme_picker.py:76-93), so Esc won't persist.
    from src.tui import app as app_mod
    from src.config import set_theme

    captured: dict = {}

    class _FakeScreen:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(app_mod, "ThemePickerScreen", _FakeScreen)

    fake_self = SimpleNamespace(
        _theme_name="dark",
        announcer=SimpleNamespace(announce=lambda *a, **k: None),
        push_screen=lambda screen, callback=None: None,
        apply_theme=lambda *a, **k: None,
        _restore_prompt_focus=lambda: None,
    )

    app_mod.ClawCodexTUI._open_theme_picker(fake_self, transcript=None)

    assert captured.get("on_persist") is set_theme
    # And the picker is still seeded the same way (no regression of preview wiring).
    assert callable(captured.get("on_preview"))
    assert captured.get("current") == "dark"

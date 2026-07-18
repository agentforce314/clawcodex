"""Tests for the ``/effort`` command (Phase 6 — port of TS local-jsx).

Ports the behavior of ``typescript/src/commands/effort/`` (``effort.tsx`` +
``index.ts``) onto the interactive-command bridge, mirroring the ``/theme`` test
layout (``tests/test_theme_command.py``). Like ``/theme``, ``/effort`` is the
**inverse** of ``/export`` at the TUI dispatch layer: the TUI keeps **intercepting**
``/effort`` (``handled=True``, ``open_dialog="effort"``) so the rich
``EffortPickerScreen`` is preserved; the ``EffortCommand`` is exercised only on
registry-consulting surfaces (REPL/SDK/listings).

Difference from ``/theme``: ``/effort`` has a **headless arg keystone** — ``/effort
high``/``current``/``help`` work with no UI (section C); only the no-args picker needs a
surface (section F).

Persistence note: ``/effort`` writes ``settings.effort`` (the validated settings
channel). It is read via ``get_settings()`` — which is **cache-backed**, unlike theme's
``load_config()`` — so the isolation fixture must invalidate the settings cache.

Sections:
  * A — metadata + registration (INTERACTIVE, name, verbatim TS description + arg hint).
  * B — bridge-safety **by type**.
  * C — headless arg paths: set level/auto/unset, invalid (no xhigh), help, current.
  * D — picker happy path (level + auto), display="user", persisted, select seeded.
  * E — picker cancel: "Cancelled" / display="user" (NOT skip), settings unchanged.
  * F — null surface: no-args picker raises → engine returns a clean error.
  * G — options shape: values == auto+levels; current option marked; raw labels.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import src.config as cfg
from src.command_system import (
    EFFORT_COMMAND,
    EffortCommand,
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
from src.settings.settings import get_settings, invalidate_settings_cache

_LOW_DESC = "Quick, straightforward implementation with minimal overhead"
_HIGH_DESC = "Comprehensive implementation with extensive testing and documentation"
_MAX_DESC = "Maximum capability with deepest reasoning"
_EXPECTED_OPTION_VALUES = ["auto", "low", "medium", "high", "xhigh", "max"]


# --------------------------------------------------------------------------- #
# Fakes / fixtures
# --------------------------------------------------------------------------- #
class FakeUIHost:
    """Scripted UI surface recording ``select`` calls (``/effort`` only uses select)."""

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
def isolated_settings(tmp_path, monkeypatch):
    """Isolate the global config + settings read-channel.

    ``/effort`` persists to ``settings.effort`` via ``set_effort`` (the default
    ConfigManager → global ``"settings"`` section) and reads it via ``get_settings()``
    (a fresh ``ConfigManager(cwd=None)``). To isolate:
      * point the global config file at ``tmp_path`` (write + read of the global level);
      * give the singleton a fresh manager rooted at ``tmp_path``;
      * neutralize ``_find_git_root`` so the cwd=None read in ``get_settings`` finds NO
        project/local config (no leakage from the real repo);
      * invalidate the (module-global) settings cache before AND after, so neither a
        prior test nor this one's writes leak across tests.
    """
    monkeypatch.setattr(cfg, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "_default_manager", cfg.ConfigManager(cwd=tmp_path))
    monkeypatch.setattr(cfg, "_find_git_root", lambda *a, **k: None)
    invalidate_settings_cache()
    yield tmp_path
    invalidate_settings_cache()


def _ctx(tmp_path: Path, *, ui=None):
    # /effort ignores the conversation (picker/args only), so we don't wire one.
    return create_command_context(workspace_root=tmp_path, cwd=tmp_path, ui=ui)


def _registry_with(*commands) -> CommandRegistry:
    reg = CommandRegistry()
    for c in commands:
        reg.register(c)
    return reg


def _persisted_effort() -> str:
    """Read ``effort`` back through the real ``get_settings()`` read-channel (fresh)."""
    invalidate_settings_cache()
    return get_settings().effort


# --------------------------------------------------------------------------- #
# A. Metadata + registration
# --------------------------------------------------------------------------- #
def test_effort_registered_in_builtins_and_aggregator():
    assert "effort" in {c.name for c in get_builtin_commands()}
    assert "effort" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_effort_metadata_mirrors_ts():
    assert isinstance(EFFORT_COMMAND, EffortCommand)
    assert EFFORT_COMMAND.name == "effort"
    # Verbatim from typescript/src/commands/effort/index.ts.
    assert EFFORT_COMMAND.description == "Set effort level for model usage"
    # TS sets argumentHint (unlike /theme); full ladder incl. xhigh.
    assert EFFORT_COMMAND.argument_hint == "[low|medium|high|xhigh|max|auto]"
    # local-jsx -> INTERACTIVE (so the remote/bridge gate blocks it by type).
    assert EFFORT_COMMAND.command_type == CommandType.INTERACTIVE
    assert EFFORT_COMMAND.is_hidden is False
    assert EFFORT_COMMAND.disable_model_invocation is False
    assert EFFORT_COMMAND.user_invocable is True


# --------------------------------------------------------------------------- #
# B. Bridge-safety BY TYPE
# --------------------------------------------------------------------------- #
def test_effort_blocked_from_bridge_by_type():
    # INTERACTIVE commands are never bridge-safe (mirrors TS local-jsx).
    assert is_bridge_safe_command(EFFORT_COMMAND) is False


# --------------------------------------------------------------------------- #
# C. Headless arg paths (the keystone — no UI needed)
# --------------------------------------------------------------------------- #
async def test_arg_sets_level_headless(isolated_settings):
    tmp_path = isolated_settings
    out = await EFFORT_COMMAND.run("high", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == f"Set effort level to high: {_HIGH_DESC}"
    assert out.display == "user"
    assert _persisted_effort() == "high"


async def test_arg_sets_max(isolated_settings):
    tmp_path = isolated_settings
    out = await EFFORT_COMMAND.run("max", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == f"Set effort level to max: {_MAX_DESC}"
    assert _persisted_effort() == "max"


async def test_arg_auto_clears(isolated_settings):
    tmp_path = isolated_settings
    await EFFORT_COMMAND.run("high", _ctx(tmp_path, ui=NullUIHost()))  # set first
    out = await EFFORT_COMMAND.run("auto", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == "Effort level set to auto"
    assert out.display == "user"
    assert _persisted_effort() == ""


async def test_arg_unset_is_auto(isolated_settings):
    tmp_path = isolated_settings
    await EFFORT_COMMAND.run("high", _ctx(tmp_path, ui=NullUIHost()))
    out = await EFFORT_COMMAND.run("unset", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == "Effort level set to auto"
    assert _persisted_effort() == ""


async def test_arg_is_case_insensitive(isolated_settings):
    tmp_path = isolated_settings
    out = await EFFORT_COMMAND.run("HIGH", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == f"Set effort level to high: {_HIGH_DESC}"
    assert _persisted_effort() == "high"


async def test_invalid_arg_does_not_persist(isolated_settings, monkeypatch):
    # Base effort contract (no workflow extension) — the `ultracode` menu option
    # and valid-args entry are covered in test_ultracode.py.
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    tmp_path = isolated_settings
    out = await EFFORT_COMMAND.run("bogus", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == (
        "Invalid argument: bogus. Valid options are: low, medium, high, xhigh, max, auto"
    )
    assert out.display == "user"
    assert _persisted_effort() == ""  # unchanged


async def test_xhigh_is_valid_and_persists(isolated_settings):
    # xhigh is a real Claude effort level (low|medium|high|xhigh|max);
    # per-model wire acceptance is handled by resolve_thinking_effort,
    # which degrades it to high on models that reject it.
    tmp_path = isolated_settings
    out = await EFFORT_COMMAND.run("xhigh", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message.startswith("Set effort level to xhigh")
    assert _persisted_effort() == "xhigh"


@pytest.mark.parametrize("arg", ["help", "-h", "--help"])
async def test_help_prints_usage_without_writing(isolated_settings, arg):
    tmp_path = isolated_settings
    out = await EFFORT_COMMAND.run(arg, _ctx(tmp_path, ui=NullUIHost()))
    assert out.message.startswith("Usage: /effort [low|medium|high|xhigh|max|auto]")
    assert out.display == "user"
    assert _persisted_effort() == ""  # help never persists


async def test_current_reports_auto_when_unset(isolated_settings):
    tmp_path = isolated_settings
    out = await EFFORT_COMMAND.run("current", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == "Effort level: auto"
    assert _persisted_effort() == ""  # reading does not write


async def test_status_reports_persisted_level(isolated_settings):
    tmp_path = isolated_settings
    await EFFORT_COMMAND.run("high", _ctx(tmp_path, ui=NullUIHost()))
    out = await EFFORT_COMMAND.run("status", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == f"Current effort level: high ({_HIGH_DESC})"


# --------------------------------------------------------------------------- #
# D. Picker happy path
# --------------------------------------------------------------------------- #
async def test_picker_sets_level(isolated_settings, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")  # base picker (no ultracode option)
    tmp_path = isolated_settings
    ui = FakeUIHost(pick="low")
    out = await EFFORT_COMMAND.run("", _ctx(tmp_path, ui=ui))
    assert isinstance(out, InteractiveOutcome)
    assert out.message == f"Set effort level to low: {_LOW_DESC}"
    assert out.display == "user"
    assert out.should_query is False
    assert _persisted_effort() == "low"
    assert ui.select_calls[0]["values"] == _EXPECTED_OPTION_VALUES


async def test_picker_auto_uses_picker_message(isolated_settings):
    tmp_path = isolated_settings
    ui = FakeUIHost(pick="auto")
    out = await EFFORT_COMMAND.run("", _ctx(tmp_path, ui=ui))
    # Picker auto != arg auto: TS effort.tsx:213 vs unsetEffortLevel:102.
    assert out.message == "Set effort level to auto: Use default effort level for your model"
    assert _persisted_effort() == ""


async def test_picker_seeds_current_from_settings(isolated_settings):
    tmp_path = isolated_settings
    await EFFORT_COMMAND.run("medium", _ctx(tmp_path, ui=NullUIHost()))  # pre-persist
    ui = FakeUIHost(pick="high")
    await EFFORT_COMMAND.run("", _ctx(tmp_path, ui=ui))
    assert ui.select_calls[0]["current"] == "medium"


async def test_picker_current_is_auto_when_unset(isolated_settings):
    tmp_path = isolated_settings
    ui = FakeUIHost(pick="high")
    await EFFORT_COMMAND.run("", _ctx(tmp_path, ui=ui))
    assert ui.select_calls[0]["current"] == "auto"


# --------------------------------------------------------------------------- #
# E. Picker cancel
# --------------------------------------------------------------------------- #
async def test_cancel_returns_user_cancelled_not_skip(isolated_settings):
    tmp_path = isolated_settings
    ui = FakeUIHost(pick=None)  # Esc
    out = await EFFORT_COMMAND.run("", _ctx(tmp_path, ui=ui))
    # TS handleCancel -> onDone('Cancelled') (no options -> model-visible). NOT skip.
    assert out.message == "Cancelled"
    assert out.display == "user"
    assert out.display != "skip"
    assert _persisted_effort() == ""  # cancel does not persist


# --------------------------------------------------------------------------- #
# F. Null surface (only the no-args picker needs a UI)
# --------------------------------------------------------------------------- #
async def test_picker_raises_on_null_surface(isolated_settings):
    tmp_path = isolated_settings
    with pytest.raises(InteractiveUnavailableError):
        await EFFORT_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost()))
    assert _persisted_effort() == ""


async def test_engine_errors_cleanly_on_null_surface(isolated_settings):
    tmp_path = isolated_settings
    reg = _registry_with(EFFORT_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    assert ctx.ui is None  # engine substitutes NullUIHost
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/effort")  # no args => picker => needs a surface

    assert result.success is False
    assert result.error is not None
    assert "interactive surface" in result.error


async def test_engine_arg_path_succeeds_headless(isolated_settings):
    # The keystone: with args, /effort works end-to-end on a NullUIHost surface.
    tmp_path = isolated_settings
    reg = _registry_with(EFFORT_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/effort high")

    assert result.success is True
    assert _persisted_effort() == "high"


# --------------------------------------------------------------------------- #
# G. Options shape
# --------------------------------------------------------------------------- #
async def test_options_shape_marks_current_and_uses_raw_labels(isolated_settings, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")  # base picker (no ultracode option)
    tmp_path = isolated_settings
    await EFFORT_COMMAND.run("high", _ctx(tmp_path, ui=NullUIHost()))  # current=high
    ui = FakeUIHost(pick="high")
    await EFFORT_COMMAND.run("", _ctx(tmp_path, ui=ui))

    call = ui.select_calls[0]
    assert call["values"] == _EXPECTED_OPTION_VALUES
    assert call["labels"] == _EXPECTED_OPTION_VALUES  # raw values as labels
    for value, desc in zip(call["values"], call["descriptions"]):
        assert desc == ("current" if value == "high" else None)

"""Tests for the ``/logo`` command (Phase 8 — port of TS local-jsx).

Mirrors the ``/theme`` test layout. ``/logo`` differs in ONE structural way: it has NO
TUI dialog, so its dispatch **falls through** (the ``/export`` pattern) rather than
being intercepted (the theme/effort/model inversion). Also includes lightweight
banner-wiring assertions (both startup banners resolve+apply the palette).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import src.config as cfg
from src.command_system import (
    LOGO_COMMAND,
    LogoCommand,
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
from src.utils.logo_palettes import (
    DEFAULT_LOGO_PALETTE,
    LOGO_PALETTE_LABELS,
    LOGO_PALETTE_NAMES,
    banner_palette,
)


class FakeUIHost:
    def __init__(self, *, pick=None):
        self._pick = pick
        self.select_calls: list[dict] = []

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
        return None

    async def display(self, title, body):
        return None


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the global config at a tmp file + a fresh manager rooted at a non-git
    tmp cwd (logo reads via ``load_config()``/``ConfigManager``, like ``/theme``)."""
    monkeypatch.setattr(cfg, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "_default_manager", cfg.ConfigManager(cwd=tmp_path))
    return tmp_path


def _ctx(tmp_path: Path, *, ui=None):
    return create_command_context(workspace_root=tmp_path, cwd=tmp_path, ui=ui)


def _registry_with(*commands) -> CommandRegistry:
    reg = CommandRegistry()
    for c in commands:
        reg.register(c)
    return reg


def _persisted_logo(tmp_path: Path):
    return cfg.ConfigManager(cwd=tmp_path).get("logoColor")


# --------------------------------------------------------------------------- #
# A. Metadata + registration
# --------------------------------------------------------------------------- #
def test_logo_registered_in_builtins_and_aggregator():
    assert "logo" in {c.name for c in get_builtin_commands()}
    assert "logo" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_logo_metadata_mirrors_ts():
    assert isinstance(LOGO_COMMAND, LogoCommand)
    assert LOGO_COMMAND.name == "logo"
    assert LOGO_COMMAND.description == "Change the startup logo color scheme"
    assert LOGO_COMMAND.argument_hint is None  # TS sets none
    assert LOGO_COMMAND.command_type == CommandType.INTERACTIVE
    assert LOGO_COMMAND.is_hidden is False
    assert LOGO_COMMAND.disable_model_invocation is False
    assert LOGO_COMMAND.user_invocable is True


# --------------------------------------------------------------------------- #
# B. Bridge-safety + dispatch FALL-THROUGH (the /export pattern)
# --------------------------------------------------------------------------- #
def test_logo_blocked_from_bridge_by_type():
    assert is_bridge_safe_command(LOGO_COMMAND) is False


def test_dispatch_local_command_falls_through_for_logo():
    # THE DIFFERENCE vs theme/effort/model: /logo has NO TUI dialog, so dispatch
    # falls through (handled=False) and the registry command runs on every surface.
    from src.tui.commands import dispatch_local_command

    res = dispatch_local_command(
        "/logo", session=None, workspace_root=Path("."), tool_registry=None
    )
    assert res.handled is False
    assert res.open_dialog is None


# --------------------------------------------------------------------------- #
# C. Picker happy path
# --------------------------------------------------------------------------- #
async def test_picker_persists_and_reports(isolated_config):
    tmp_path = isolated_config
    ui = FakeUIHost(pick="ocean")
    out = await LOGO_COMMAND.run("", _ctx(tmp_path, ui=ui))

    assert isinstance(out, InteractiveOutcome)
    assert out.message == "Startup logo set to Ocean blue. Visible on next launch."
    assert out.display == "user"  # TS no-options onDone -> model-visible
    assert _persisted_logo(tmp_path) == "ocean"
    call = ui.select_calls[0]
    assert call["values"] == LOGO_PALETTE_NAMES
    assert call["labels"] == [LOGO_PALETTE_LABELS[n] for n in LOGO_PALETTE_NAMES]


# --------------------------------------------------------------------------- #
# D. Cancel
# --------------------------------------------------------------------------- #
async def test_cancel_returns_system_dismissed(isolated_config):
    tmp_path = isolated_config
    ui = FakeUIHost(pick=None)
    out = await LOGO_COMMAND.run("", _ctx(tmp_path, ui=ui))

    assert out.message == "Logo picker dismissed"
    assert out.display == "system"
    assert out.display != "skip"
    assert _persisted_logo(tmp_path) is None  # cancel does not persist


# --------------------------------------------------------------------------- #
# E. Null surface + args ignored
# --------------------------------------------------------------------------- #
async def test_run_raises_on_null_surface(isolated_config):
    tmp_path = isolated_config
    with pytest.raises(InteractiveUnavailableError):
        await LOGO_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost()))
    assert _persisted_logo(tmp_path) is None


async def test_engine_errors_cleanly_on_null_surface(isolated_config):
    tmp_path = isolated_config
    reg = _registry_with(LOGO_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    assert ctx.ui is None  # engine substitutes NullUIHost
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/logo")

    assert result.success is False
    assert result.error is not None
    assert "interactive surface" in result.error


async def test_args_are_ignored(isolated_config):
    tmp_path = isolated_config
    ui = FakeUIHost(pick="forest")
    out = await LOGO_COMMAND.run("ocean extra args", _ctx(tmp_path, ui=ui))
    assert out.message == "Startup logo set to Forest green. Visible on next launch."
    assert _persisted_logo(tmp_path) == "forest"  # the PICK wins, not the args
    assert len(ui.select_calls) == 1


# --------------------------------------------------------------------------- #
# F. Seed current from persisted palette
# --------------------------------------------------------------------------- #
async def test_picker_current_defaults_when_unset(isolated_config):
    tmp_path = isolated_config
    ui = FakeUIHost(pick="ocean")
    await LOGO_COMMAND.run("", _ctx(tmp_path, ui=ui))
    assert ui.select_calls[0]["current"] == DEFAULT_LOGO_PALETTE  # "sunset"


async def test_picker_seeds_current_from_persisted(isolated_config):
    tmp_path = isolated_config
    cfg.set_logo_color("ocean")
    ui = FakeUIHost(pick="forest")
    await LOGO_COMMAND.run("", _ctx(tmp_path, ui=ui))

    call = ui.select_calls[0]
    assert call["current"] == "ocean"
    # Exactly the current option carries the "current" marker.
    for value, desc in zip(call["values"], call["descriptions"]):
        assert desc == ("current" if value == "ocean" else None)


# --------------------------------------------------------------------------- #
# G. Banner wiring (both startup banners resolve + apply the palette)
# --------------------------------------------------------------------------- #
def test_repl_banner_wires_palette():
    from src.repl.core import ClawcodexREPL

    src_text = inspect.getsource(ClawcodexREPL._print_startup_header)
    assert "banner_palette" in src_text
    assert "mascot_gradient_text" in src_text


def test_tui_header_wires_palette_and_applies_border(isolated_config):
    tmp_path = isolated_config
    cfg.set_logo_color("forest")

    from src.tui.widgets.header import StartupHeader

    header = StartupHeader(
        version="1.2.3",
        model="claude-sonnet-4-6",
        provider="anthropic",
        workspace_root=tmp_path,
    )
    panel = header._render_banner()
    # The Panel border is the forest palette's border hex (best-effort wiring applied).
    assert panel.border_style == banner_palette("forest").border


def test_tui_header_falls_back_on_bogus_logo(isolated_config, monkeypatch):
    tmp_path = isolated_config
    cfg.set_logo_color("not-a-real-palette")

    from src.tui.widgets.header import StartupHeader

    # Bogus name resolves to the default palette (no crash).
    header = StartupHeader(
        version="1.2.3",
        model="m",
        provider="anthropic",
        workspace_root=tmp_path,
    )
    panel = header._render_banner()
    assert panel.border_style == banner_palette(None).border  # default sunset

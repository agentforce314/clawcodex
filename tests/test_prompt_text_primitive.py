"""Tests for the ``prompt_text`` UIHost primitive (Phase 4, P1).

``prompt_text`` is the second mutating primitive on the surface-agnostic
``UIHost`` port (after ``select``). It reads a single free-text line and lands
with its first consumer, ``/export``. The load-bearing divergence from
``select`` — pinned across every surface here — is the empty case:

  * ``select``: an empty REPL line / no selection means **cancel** (``None``).
  * ``prompt_text``: an empty submit is a **valid empty string** (``''``),
    mirroring TS ``TextInput.onSubmit('')``. ``None`` is returned *only* on a
    real cancel (Esc / EOF / Ctrl-C).

Coverage, per surface:
  * ``NullUIHost`` — raises ``InteractiveUnavailableError`` (SDK / headless).
  * ``ReplUIHost`` — typed value; empty ⇒ ``''`` (NOT cancel, NOT the default);
    whitespace preserved; EOF / Ctrl-C ⇒ ``None``; default/placeholder hint.
  * ``TextualUIHost`` — relays the modal value (incl. ``''``), relays cancel
    ``None``, serializes overlapping prompts via the per-host lock.
  * Engine seam — a consumer ``InteractiveCommand`` driving ``prompt_text``
    round-trips through ``CommandEngine.execute`` (value, empty ⇒ value not
    skip, null-surface ⇒ clean error).
  * ``GenericInputScreen`` (real Textual, via Pilot) — empty Enter ⇒ ``''``,
    typed Enter ⇒ value, prefilled default ⇒ default, Esc ⇒ ``None``.
"""
from __future__ import annotations

import asyncio

import pytest

from src.command_system import create_command_context
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.types import (
    InteractiveCommand,
    InteractiveOutcome,
    InteractiveUnavailableError,
    NullUIHost,
)
from src.repl.ui_host import ReplUIHost
from src.tui.screens.generic_input import GenericInputScreen
from src.tui.ui_host import TextualUIHost


# --------------------------------------------------------------------------- #
# A. NullUIHost contract — mutating primitive raises (mirrors select)
# --------------------------------------------------------------------------- #
async def test_null_ui_prompt_text_raises_interactive_unavailable():
    host = NullUIHost()
    with pytest.raises(InteractiveUnavailableError):
        await host.prompt_text("Filename", default="x", placeholder="p")


# --------------------------------------------------------------------------- #
# B. ReplUIHost — empty ⇒ '' (the divergence), and every cancel path
# --------------------------------------------------------------------------- #
async def test_repl_prompt_text_returns_typed_value():
    host = ReplUIHost(safe_input=lambda _p: "report.md", console=None)
    assert await host.prompt_text("Filename") == "report.md"


async def test_repl_prompt_text_empty_is_empty_string_not_cancel():
    # THE divergence from select: an empty line submits '' (valid), not None.
    host = ReplUIHost(safe_input=lambda _p: "", console=None)
    assert await host.prompt_text("Filename") == ""


async def test_repl_prompt_text_does_not_substitute_default_on_empty():
    # Empty ⇒ '' even when a default exists — the adapter never auto-fills the
    # default (both surfaces keep "empty ⇒ ''"; the default is only a hint).
    host = ReplUIHost(safe_input=lambda _p: "", console=None)
    assert await host.prompt_text("Filename", default="conversation.md") == ""


async def test_repl_prompt_text_preserves_whitespace_raw():
    # The raw line is returned untouched (no strip) — distinct from select,
    # which strips before validating the numeric choice.
    host = ReplUIHost(safe_input=lambda _p: "  spaced name  ", console=None)
    assert await host.prompt_text("Filename") == "  spaced name  "


async def test_repl_prompt_text_none_raw_coerced_to_empty():
    # Defensive: a safe_input returning None still yields '' (a submit), never
    # a surprise None that the command body would read as a cancel.
    host = ReplUIHost(safe_input=lambda _p: None, console=None)
    assert await host.prompt_text("Filename") == ""


async def test_repl_prompt_text_eof_cancels():
    def _raise(_p):
        raise EOFError

    host = ReplUIHost(safe_input=_raise, console=None)
    assert await host.prompt_text("Filename") is None


async def test_repl_prompt_text_keyboard_interrupt_cancels():
    def _raise(_p):
        raise KeyboardInterrupt

    host = ReplUIHost(safe_input=_raise, console=None)
    assert await host.prompt_text("Filename") is None


async def test_repl_prompt_text_surfaces_default_as_hint():
    captured = {}

    def _input(prompt):
        captured["prompt"] = prompt
        return "typed.md"

    host = ReplUIHost(safe_input=_input, console=None)
    await host.prompt_text("Save as", default="out.md", placeholder="path")
    # Default takes precedence over placeholder in the inline hint.
    assert "Save as" in captured["prompt"]
    assert "[out.md]" in captured["prompt"]
    assert "(path)" not in captured["prompt"]


async def test_repl_prompt_text_surfaces_placeholder_when_no_default():
    captured = {}

    def _input(prompt):
        captured["prompt"] = prompt
        return ""

    host = ReplUIHost(safe_input=_input, console=None)
    await host.prompt_text("Save as", placeholder="path/to/file")
    assert "(path/to/file)" in captured["prompt"]


# --------------------------------------------------------------------------- #
# C. TextualUIHost — relay (incl. '') + cancel + lock serialization
# --------------------------------------------------------------------------- #
async def test_tui_prompt_text_relays_typed_value_and_constructs_screen():
    captured = {}

    class _App:
        async def push_screen_wait(self, screen):
            captured["title"] = screen.title_text
            captured["default"] = screen._default
            captured["placeholder"] = screen._placeholder
            return "typed.md"

    host = TextualUIHost(_App())
    result = await host.prompt_text("Filename", default="d.md", placeholder="p")

    assert result == "typed.md"
    assert captured == {"title": "Filename", "default": "d.md", "placeholder": "p"}


async def test_tui_prompt_text_relays_empty_string_not_coerced():
    # An empty submit from the screen survives the adapter as '' (not None).
    class _App:
        async def push_screen_wait(self, screen):
            return ""

    assert await TextualUIHost(_App()).prompt_text("t") == ""


async def test_tui_prompt_text_relays_cancel_none():
    class _App:
        async def push_screen_wait(self, screen):
            return None

    assert await TextualUIHost(_App()).prompt_text("t") is None


async def test_tui_prompt_text_serializes_overlapping_prompts():
    # Same per-host lock contract as select (plan §8.1): the non-exclusive
    # worker could overlap two prompts; the lock makes the second queue.
    state = {"in_flight": 0, "max": 0}

    class _App:
        async def push_screen_wait(self, screen):
            state["in_flight"] += 1
            state["max"] = max(state["max"], state["in_flight"])
            await asyncio.sleep(0.01)  # hold the modal "open"
            state["in_flight"] -= 1
            return screen._default

    host = TextualUIHost(_App())
    await asyncio.gather(
        host.prompt_text("t", default="a"),
        host.prompt_text("t", default="b"),
    )
    assert state["max"] == 1  # never two modals open at once


# --------------------------------------------------------------------------- #
# D. Engine path — a consumer command drives prompt_text end-to-end
# --------------------------------------------------------------------------- #
class _PromptingFakeUIHost:
    """Scripts a ``prompt_text`` return value (or None) and records the call —
    the free-text analogue of the bridge's ``FakeUIHost`` (which scripts
    ``select``)."""

    def __init__(self, value):
        self._value = value
        self.calls: list[dict] = []

    async def prompt_text(self, title, *, default="", placeholder=None):
        self.calls.append(
            {"title": title, "default": default, "placeholder": placeholder}
        )
        return self._value


class _PromptStub(InteractiveCommand):
    """A minimal consumer: awaits one ``prompt_text`` and echoes the value.
    ``None`` (cancel) maps to skip; ``''`` is a value and echoes ``got:``."""

    async def run(self, args, context):
        value = await context.ui.prompt_text("Filename", default="d.md")
        if value is None:
            return InteractiveOutcome.skip()
        return InteractiveOutcome(message=f"got:{value}", display="system")


def _registry_with(*commands) -> CommandRegistry:
    reg = CommandRegistry()
    for c in commands:
        reg.register(c)
    return reg


async def test_engine_routes_prompt_text_value_to_text_result(tmp_path):
    # Proves engine -> command.run -> ctx.ui.prompt_text -> outcome round-trips
    # the typed value (and the kwargs reach the host).
    ui = _PromptingFakeUIHost("report.md")
    reg = _registry_with(_PromptStub(name="askname", description="d"))
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path, ui=ui)
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/askname")

    assert result.success is True
    assert result.result_type == "text"
    assert result.text == "got:report.md"
    assert ui.calls[0] == {
        "title": "Filename",
        "default": "d.md",
        "placeholder": None,
    }


async def test_engine_prompt_text_empty_string_is_a_value_not_skip(tmp_path):
    # The divergence at the engine seam: an empty submit ('') flows through as
    # a VALUE (text result), NOT a cancel/skip.
    ui = _PromptingFakeUIHost("")
    reg = _registry_with(_PromptStub(name="askname", description="d"))
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path, ui=ui)
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/askname")

    assert result.success is True
    assert result.result_type == "text"
    assert result.text == "got:"


async def test_engine_prompt_text_on_null_surface_reports_clean_error(tmp_path):
    # No UI wired -> engine substitutes NullUIHost -> prompt_text raises
    # InteractiveUnavailableError -> engine returns a clean error result (no
    # crash). Mirrors the select null-surface contract.
    reg = _registry_with(_PromptStub(name="askname", description="d"))
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    assert ctx.ui is None  # nothing wired; engine substitutes NullUIHost
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/askname")

    assert result.success is False
    assert "interactive surface" in (result.error or "")


# --------------------------------------------------------------------------- #
# E. GenericInputScreen — construction + real Textual behavior via Pilot
# --------------------------------------------------------------------------- #
from textual.app import App, ComposeResult  # noqa: E402
from textual.screen import Screen  # noqa: E402
from textual.widgets import Static  # noqa: E402


class _Host(Screen):
    def compose(self) -> ComposeResult:
        yield Static("host")


class _DialogHost(App):
    def on_mount(self) -> None:
        self.push_screen(_Host())


def _push(app: App, screen) -> asyncio.Future:
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _callback(result):
        if not future.done():
            future.set_result(result)

    app.push_screen(screen, callback=_callback)
    return future


def test_generic_input_screen_stores_construction_args():
    # App-free: construction does not build the Input (that's build_body).
    s = GenericInputScreen(title="Save as", default="d.md", placeholder="path")
    assert s.title_text == "Save as"
    assert s._default == "d.md"
    assert s._placeholder == "path"
    assert s._input is None
    assert s.footer_hint == "Enter to submit · Esc to cancel"


@pytest.mark.asyncio
async def test_generic_input_screen_empty_enter_submits_empty_string():
    # The load-bearing divergence at the real screen: Enter on an empty field
    # dismisses with '' (a valid submit), NOT None (which is cancel).
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        fut = _push(app, GenericInputScreen(title="Filename"))
        await pilot.pause()
        await pilot.press("enter")
        assert await fut == ""


@pytest.mark.asyncio
async def test_generic_input_screen_typed_enter_submits_value():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        fut = _push(app, GenericInputScreen(title="Filename"))
        await pilot.pause()
        await pilot.press("r", "e", "p", "o", "r", "t")
        await pilot.press("enter")
        assert await fut == "report"


@pytest.mark.asyncio
async def test_generic_input_screen_prefilled_default_submits_default():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        fut = _push(app, GenericInputScreen(title="Filename", default="conversation.md"))
        await pilot.pause()
        await pilot.press("enter")  # accept the prefilled default unchanged
        assert await fut == "conversation.md"


@pytest.mark.asyncio
async def test_generic_input_screen_escape_resolves_none():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        fut = _push(app, GenericInputScreen(title="Filename", default="x"))
        await pilot.pause()
        await pilot.press("escape")
        assert await fut is None

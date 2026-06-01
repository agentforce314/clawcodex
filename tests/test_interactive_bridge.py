"""Tests for the interactive-command bridge slice (gap P0-3).

Ports the TS ``local-jsx`` decision: one command body drives a numbered REPL
menu *and* a Textual modal through the surface-agnostic ``UIHost`` port, and
(raising) the SDK / non-interactive ``NullUIHost``. ``/permissions`` is the
reference command — a single ``select`` over the user-facing permission modes,
persisted via the reactive ``AppState`` store.

Plan: my-docs/get-parity-by-folder/commands-phase2-interactive-bridge-plan.md
(§7 test plan). Covers, per surface and at the seams:

  * ``PermissionsCommand.run`` — pick persists + fires the change handler;
    cancel → skip, no persist; pick==current → unchanged; no store → reported.
  * Engine ``_execute_interactive`` arm — text result, skip, propagation of
    ``display`` / ``should_query`` / ``meta_messages`` (which the LOCAL arm
    hardcodes away), null-surface clean error, non-``InteractiveOutcome`` guard.
  * ``NullUIHost`` contract — ``select`` raises, ``display`` no-ops.
  * ``ReplUIHost`` numbered-menu mapping — valid pick, and every cancel path
    (empty / non-numeric / out-of-range / EOF / no options); menu rendering.
  * ``TextualUIHost`` — relays the modal's value, serializes via the lock,
    ``display`` → toast.
  * Registry discoverability + bridge-safety **by type** (an INTERACTIVE
    command whose name IS allowlisted stays blocked) + ``dispatch_local_command``
    falls through for ``/permissions``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.command_system import (
    BRIDGE_SAFE_COMMANDS,
    PERMISSIONS_COMMAND,
    PermissionsCommand,
    create_command_context,
    get_builtin_commands,
    get_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine, CommandResult
from src.command_system.registry import CommandRegistry
from src.command_system.types import (
    CommandType,
    InteractiveCommand,
    InteractiveOutcome,
    InteractiveUnavailableError,
    NullUIHost,
    UIOption,
)
from src.repl.ui_host import ReplUIHost
from src.state.app_state import (
    create_app_state_store,
    set_permission_mode_listener,
)
from src.tui.ui_host import TextualUIHost


# --------------------------------------------------------------------------- #
# Fakes / fixtures
# --------------------------------------------------------------------------- #
class FakeUIHost:
    """Records ``select`` calls and returns a scripted pick (or None)."""

    def __init__(self, pick: str | None) -> None:
        self._pick = pick
        self.select_calls: list[dict] = []
        self.display_calls: list[tuple[str, str]] = []

    async def select(self, title, options, *, current=None):
        self.select_calls.append(
            {"title": title, "values": [o.value for o in options], "current": current}
        )
        return self._pick

    async def display(self, title, body):
        self.display_calls.append((title, body))


@pytest.fixture(autouse=True)
def _reset_permission_listener():
    """The permission-mode listener is a module global; reset it after each
    test so a registered observer never leaks into the next test's store."""
    yield
    set_permission_mode_listener(None)


def _ctx(store, ui, tmp_path: Path):
    return create_command_context(
        workspace_root=tmp_path, cwd=tmp_path, app_state_store=store, ui=ui
    )


def _registry_with(*commands) -> CommandRegistry:
    reg = CommandRegistry()
    for c in commands:
        reg.register(c)
    return reg


# --------------------------------------------------------------------------- #
# A. PermissionsCommand.run — behavior against the reactive store
# --------------------------------------------------------------------------- #
async def test_pick_persists_and_fires_change_handler(tmp_path):
    store = create_app_state_store()
    fired: list[str] = []
    set_permission_mode_listener(fired.append)
    ui = FakeUIHost("plan")

    outcome = await PERMISSIONS_COMMAND.run("", _ctx(store, ui, tmp_path))

    assert outcome.message == "Permission mode set to plan."
    assert outcome.display == "system"
    assert store.get_state().permission_mode == "plan"
    # The persist fired the centralized change handler -> external listener.
    assert fired == ["plan"]


async def test_cancel_returns_skip_and_does_not_persist(tmp_path):
    store = create_app_state_store()
    fired: list[str] = []
    set_permission_mode_listener(fired.append)
    ui = FakeUIHost(None)  # user pressed Esc / Enter

    outcome = await PERMISSIONS_COMMAND.run("", _ctx(store, ui, tmp_path))

    assert outcome.display == "skip"
    assert outcome.message is None
    assert store.get_state().permission_mode == "default"  # untouched
    assert fired == []  # no persist -> no notification


async def test_pick_equal_to_current_is_unchanged(tmp_path):
    store = create_app_state_store()  # default == "default"
    fired: list[str] = []
    set_permission_mode_listener(fired.append)
    ui = FakeUIHost("default")

    outcome = await PERMISSIONS_COMMAND.run("", _ctx(store, ui, tmp_path))

    assert outcome.message == "Permission mode unchanged (default)."
    assert outcome.display == "system"
    assert store.get_state().permission_mode == "default"
    assert fired == []  # short-circuits before set_state


async def test_no_store_reports_unavailable(tmp_path):
    # A surface that wired a UI but no reactive store: report honestly rather
    # than silently no-op (mirrors NullUIHost's honest-failure stance).
    ui = FakeUIHost("plan")
    outcome = await PERMISSIONS_COMMAND.run("", _ctx(None, ui, tmp_path))

    assert outcome.message == "Permission mode unavailable (no app state store)."
    assert outcome.display == "system"


async def test_select_receives_current_and_cycle_ordered_options(tmp_path):
    store = create_app_state_store()
    ui = FakeUIHost(None)
    await PERMISSIONS_COMMAND.run("", _ctx(store, ui, tmp_path))

    call = ui.select_calls[0]
    assert call["title"] == "Permission mode"
    assert call["current"] == "default"
    # Shift+Tab cycle order; internal modes (dontAsk/auto/bubble) excluded.
    assert call["values"] == ["default", "acceptEdits", "plan", "bypassPermissions"]


# --------------------------------------------------------------------------- #
# B. Engine _execute_interactive arm
# --------------------------------------------------------------------------- #
async def test_engine_routes_permissions_to_text_result(tmp_path):
    store = create_app_state_store()
    reg = _registry_with(PERMISSIONS_COMMAND)
    ui = FakeUIHost("acceptEdits")
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=_ctx(store, ui, tmp_path))

    result = await eng.execute("/permissions")

    assert result.success is True
    assert result.result_type == "text"
    assert result.text == "Permission mode set to acceptEdits."
    assert result.display == "system"
    assert store.get_state().permission_mode == "acceptEdits"


async def test_engine_maps_skip_outcome_to_skip_result(tmp_path):
    store = create_app_state_store()
    reg = _registry_with(PERMISSIONS_COMMAND)
    eng = CommandEngine(
        registry=reg, workspace_root=tmp_path, context=_ctx(store, FakeUIHost(None), tmp_path)
    )

    result = await eng.execute("/permissions")

    assert result.success is True
    assert result.result_type == "skip"


async def test_engine_propagates_display_should_query_and_meta(tmp_path):
    # The keystone difference from the LOCAL arm: it hardcodes display=system,
    # should_query=False and drops meta_messages. The INTERACTIVE arm must
    # propagate whatever the outcome carries.
    class _Stub(InteractiveCommand):
        async def run(self, args, context):
            return InteractiveOutcome(
                message="hi",
                display="user",
                should_query=True,
                meta_messages=["m1", "m2"],
            )

    reg = _registry_with(_Stub(name="stub", description="d"))
    eng = CommandEngine(
        registry=reg, workspace_root=tmp_path,
        context=_ctx(None, FakeUIHost(None), tmp_path),
    )

    result = await eng.execute("/stub")

    assert result.success is True
    assert result.text == "hi"
    assert result.display == "user"
    assert result.should_query is True
    assert result.meta_messages == ["m1", "m2"]


async def test_engine_null_surface_returns_clean_error(tmp_path):
    # No ui wired anywhere -> engine substitutes NullUIHost, whose select
    # raises; the arm turns that into an error CommandResult, not a crash.
    store = create_app_state_store()
    reg = _registry_with(PERMISSIONS_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path, app_state_store=store)
    assert ctx.ui is None  # nothing wired
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/permissions")

    assert result.success is False
    assert "interactive surface" in (result.error or "")
    assert store.get_state().permission_mode == "default"  # nothing persisted


async def test_engine_guards_non_interactive_outcome(tmp_path):
    class _Bad(InteractiveCommand):
        async def run(self, args, context):
            return "not an outcome"  # type: ignore[return-value]

    reg = _registry_with(_Bad(name="bad", description="d"))
    eng = CommandEngine(
        registry=reg, workspace_root=tmp_path,
        context=_ctx(None, FakeUIHost(None), tmp_path),
    )

    result = await eng.execute("/bad")

    assert result.success is False
    assert "expected InteractiveOutcome" in (result.error or "")


# --------------------------------------------------------------------------- #
# C. NullUIHost contract
# --------------------------------------------------------------------------- #
async def test_null_ui_select_raises_interactive_unavailable():
    host = NullUIHost()
    with pytest.raises(InteractiveUnavailableError):
        await host.select("t", [UIOption("a", "A")], current=None)


async def test_null_ui_display_is_noop():
    host = NullUIHost()
    assert await host.display("t", "body") is None


# --------------------------------------------------------------------------- #
# D. ReplUIHost — numbered-menu mapping + every cancel path
# --------------------------------------------------------------------------- #
_OPTS = [
    UIOption("default", "default", "Prompt before each tool's first use"),
    UIOption("acceptEdits", "acceptEdits", "Auto-accept edits"),
    UIOption("plan", "plan", "Plan only"),
    UIOption("bypassPermissions", "bypassPermissions", "Skip prompts"),
]


async def test_repl_adapter_valid_pick_maps_to_value():
    host = ReplUIHost(safe_input=lambda _p: "3", console=None)
    assert await host.select("Permission mode", _OPTS, current="default") == "plan"


@pytest.mark.parametrize("raw", ["", "   ", "abc", "0", "5", "-1", "2.5"])
async def test_repl_adapter_cancel_paths_return_none(raw):
    # empty / whitespace / non-numeric / out-of-range (low, high, negative) /
    # float -> all collapse to cancel (None), never an accidental pick.
    host = ReplUIHost(safe_input=lambda _p: raw, console=None)
    assert await host.select("t", _OPTS) is None


async def test_repl_adapter_eof_cancels():
    def _raise(_p):
        raise EOFError

    host = ReplUIHost(safe_input=_raise, console=None)
    assert await host.select("t", _OPTS) is None


async def test_repl_adapter_keyboard_interrupt_cancels():
    def _raise(_p):
        raise KeyboardInterrupt

    host = ReplUIHost(safe_input=_raise, console=None)
    assert await host.select("t", _OPTS) is None


async def test_repl_adapter_empty_options_returns_none():
    host = ReplUIHost(safe_input=lambda _p: "1", console=None)
    assert await host.select("t", []) is None


async def test_repl_adapter_renders_numbered_menu_with_current_and_desc():
    class _Console:
        def __init__(self):
            self.lines: list[str] = []

        def print(self, text=""):
            self.lines.append(text)

    console = _Console()
    host = ReplUIHost(safe_input=lambda _p: "", console=console)
    await host.select("Permission mode", _OPTS, current="plan")

    blob = "\n".join(console.lines)
    assert "Permission mode" in blob
    assert "1. default" in blob
    assert "Prompt before each tool's first use" in blob  # description shown
    assert "(current)" in blob
    # The current marker sits on the selected row (plan), not on default.
    plan_line = next(ln for ln in console.lines if "3. plan" in ln)
    assert "(current)" in plan_line


# --------------------------------------------------------------------------- #
# E. TextualUIHost — relay, serialization, display
# --------------------------------------------------------------------------- #
async def test_tui_adapter_relays_modal_value():
    captured = {}

    class _App:
        async def push_screen_wait(self, screen):
            captured["title"] = screen.title_text
            captured["values"] = [o.value for o in screen._options]
            captured["current"] = screen._current
            return "plan"

    host = TextualUIHost(_App())
    result = await host.select("Permission mode", _OPTS, current="default")

    assert result == "plan"
    assert captured["title"] == "Permission mode"
    assert captured["values"] == [o.value for o in _OPTS]
    assert captured["current"] == "default"


async def test_tui_adapter_serializes_overlapping_selects():
    # Plan §8.1: the slash-cmd worker is non-exclusive, so two interactive
    # commands could overlap; the per-host lock must make the second queue
    # rather than stack a nested modal.
    state = {"in_flight": 0, "max": 0}

    class _App:
        async def push_screen_wait(self, screen):
            state["in_flight"] += 1
            state["max"] = max(state["max"], state["in_flight"])
            await asyncio.sleep(0.01)  # hold the modal "open"
            state["in_flight"] -= 1
            return screen._current

    host = TextualUIHost(_App())
    await asyncio.gather(
        host.select("t", _OPTS, current="a"),
        host.select("t", _OPTS, current="b"),
    )
    assert state["max"] == 1  # never two modals open at once


async def test_tui_adapter_display_notifies():
    calls = []

    class _App:
        def notify(self, body, title=None):
            calls.append((title, body))

    host = TextualUIHost(_App())
    await host.display("Title", "Body")
    assert calls == [("Title", "Body")]


# --------------------------------------------------------------------------- #
# F. Registry discoverability + bridge-safety BY TYPE + dispatch fall-through
# --------------------------------------------------------------------------- #
def test_permissions_registered_in_builtins_and_aggregator():
    assert "permissions" in {c.name for c in get_builtin_commands()}
    assert "permissions" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_permissions_command_metadata():
    assert isinstance(PERMISSIONS_COMMAND, PermissionsCommand)
    assert PERMISSIONS_COMMAND.name == "permissions"
    assert PERMISSIONS_COMMAND.command_type == CommandType.INTERACTIVE
    assert PERMISSIONS_COMMAND.user_invocable is True


def test_interactive_blocked_by_type_even_when_name_allowlisted():
    # The bridge-safety gate is structural: an INTERACTIVE command whose name
    # IS in BRIDGE_SAFE_COMMANDS must still be blocked (naming can't unblock a
    # UI-rendering command). Guards against a future allowlist edit leaking one.
    class _Sneaky(InteractiveCommand):
        async def run(self, args, context):  # pragma: no cover - never run
            return InteractiveOutcome.skip()

    allowlisted_name = next(iter(BRIDGE_SAFE_COMMANDS))  # e.g. "compact"
    sneaky = _Sneaky(name=allowlisted_name, description="d")
    assert sneaky.name in BRIDGE_SAFE_COMMANDS
    assert is_bridge_safe_command(sneaky) is False
    # And the real command is blocked too.
    assert is_bridge_safe_command(PERMISSIONS_COMMAND) is False


def test_dispatch_local_command_falls_through_for_permissions():
    # The TUI's direct-dispatch table must NOT claim /permissions; it has to
    # fall through to the async registry path (where the interactive arm lives).
    from src.tui.commands import dispatch_local_command

    res = dispatch_local_command(
        "/permissions", session=None, workspace_root=Path("."), tool_registry=None
    )
    assert res.handled is False

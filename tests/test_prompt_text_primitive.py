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
  * Engine seam — a consumer ``InteractiveCommand`` driving ``prompt_text``
    round-trips through ``CommandEngine.execute`` (value, empty ⇒ value not
    skip, null-surface ⇒ clean error).
"""
from __future__ import annotations


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


# --------------------------------------------------------------------------- #
# A. NullUIHost contract — mutating primitive raises (mirrors select)
# --------------------------------------------------------------------------- #
async def test_null_ui_prompt_text_raises_interactive_unavailable():
    host = NullUIHost()
    with pytest.raises(InteractiveUnavailableError):
        await host.prompt_text("Filename", default="x", placeholder="p")


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



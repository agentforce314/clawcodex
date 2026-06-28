"""Tests for the ``/output-style`` deprecation command (Class A port).

Ports ``typescript/src/commands/output-style/`` — a ``local-jsx`` command that
renders nothing interactive and just emits a deprecation notice via
``onDone(..., { display: 'system' })``. The Python port is an
:class:`InteractiveCommand` (the ``local-jsx`` analogue, blocked remotely by
type) whose :meth:`run` returns the notice *without* touching ``context.ui``.

The keystone difference from the ``/permissions`` exemplar: because ``run`` never
calls ``ui.select``, the command is **surface-independent** — it produces the
same text on any surface, including the headless ``NullUIHost`` (SDK) surface
where ``select`` would raise. The engine test on a null surface is what
pins that guarantee.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.command_system import (
    OUTPUT_STYLE_COMMAND,
    OutputStyleCommand,
    create_command_context,
    get_builtin_commands,
    get_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.types import CommandType, InteractiveOutcome

# Verbatim from typescript/src/commands/output-style/output-style.tsx.
_EXPECTED = (
    "/output-style has been deprecated. Use /config to change your output "
    "style, or set it in your settings file. Changes take effect on the next "
    "session."
)


class _RecordingUIHost:
    """A wired UI surface that records calls — used to prove the command
    never touches it (no ``select`` / ``display``)."""

    def __init__(self) -> None:
        self.select_calls: list[tuple] = []
        self.display_calls: list[tuple] = []

    async def select(self, title, options, *, current=None):  # pragma: no cover
        self.select_calls.append((title, options, current))
        return None

    async def display(self, title, body):  # pragma: no cover
        self.display_calls.append((title, body))


def _ctx(tmp_path: Path, ui=None):
    return create_command_context(
        workspace_root=tmp_path, cwd=tmp_path, ui=ui
    )


def _registry_with(*commands) -> CommandRegistry:
    reg = CommandRegistry()
    for c in commands:
        reg.register(c)
    return reg


# --------------------------------------------------------------------------- #
# A. Metadata + registration
# --------------------------------------------------------------------------- #
def test_output_style_registered_in_builtins_and_aggregator():
    # Hidden commands are NOT filtered by the aggregator (only availability /
    # is_enabled are), so it surfaces in get_commands like any other builtin.
    assert "output-style" in {c.name for c in get_builtin_commands()}
    assert "output-style" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_output_style_metadata_mirrors_ts():
    assert isinstance(OUTPUT_STYLE_COMMAND, OutputStyleCommand)
    assert OUTPUT_STYLE_COMMAND.name == "output-style"
    assert OUTPUT_STYLE_COMMAND.description == (
        "Deprecated: use /config to change output style"
    )
    # local-jsx -> INTERACTIVE (so the remote-safety gate blocks it by type).
    assert OUTPUT_STYLE_COMMAND.command_type == CommandType.INTERACTIVE
    # TS sets only type/name/description/isHidden; everything else defaults.
    assert OUTPUT_STYLE_COMMAND.is_hidden is True
    assert OUTPUT_STYLE_COMMAND.disable_model_invocation is False
    assert OUTPUT_STYLE_COMMAND.user_invocable is True


# --------------------------------------------------------------------------- #
# B. run() — surface-independent, never touches ctx.ui
# --------------------------------------------------------------------------- #
async def test_run_returns_deprecation_notice_as_system(tmp_path):
    ui = _RecordingUIHost()
    outcome = await OUTPUT_STYLE_COMMAND.run("", _ctx(tmp_path, ui))

    assert isinstance(outcome, InteractiveOutcome)
    assert outcome.message == _EXPECTED
    assert outcome.display == "system"
    assert outcome.should_query is False
    # Crucially: it rendered nothing interactive (no select / no display).
    assert ui.select_calls == []
    assert ui.display_calls == []


async def test_run_works_with_no_ui_wired(tmp_path):
    # No UI on the context at all -> still fine, because run() never reaches
    # for ctx.ui. This is the SDK / headless surface.
    outcome = await OUTPUT_STYLE_COMMAND.run("", _ctx(tmp_path, ui=None))
    assert outcome.message == _EXPECTED
    assert outcome.display == "system"


async def test_run_ignores_arguments(tmp_path):
    # TS ``call(onDone)`` takes no args; passing some must not change behavior.
    outcome = await OUTPUT_STYLE_COMMAND.run("anything here", _ctx(tmp_path))
    assert outcome.message == _EXPECTED
    assert outcome.display == "system"


# --------------------------------------------------------------------------- #
# C. Engine INTERACTIVE path — reachable + correct on a NULL surface
# --------------------------------------------------------------------------- #
async def test_engine_routes_to_text_result_on_null_surface(tmp_path):
    # Unlike /permissions (which calls ui.select and so ERRORS when the engine
    # substitutes NullUIHost), output-style yields a clean text result with no
    # UI wired — proving the deprecation reaches users on every surface.
    reg = _registry_with(OUTPUT_STYLE_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    assert ctx.ui is None  # nothing wired; engine will substitute NullUIHost
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/output-style")

    assert result.success is True
    assert result.result_type == "text"
    assert result.text == _EXPECTED
    assert result.display == "system"


# --------------------------------------------------------------------------- #
# D. Bridge-safety BY TYPE
# --------------------------------------------------------------------------- #
def test_output_style_blocked_from_remote_by_type():
    # INTERACTIVE commands are never bridge-safe (mirrors TS, where local-jsx is
    # always remote-blocked) — even though this one renders no UI.
    assert is_bridge_safe_command(OUTPUT_STYLE_COMMAND) is False

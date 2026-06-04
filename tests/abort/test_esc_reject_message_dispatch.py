"""Regression tests for ESC-cancelled tool_results in the production path.

When the user presses ESC mid-Bash, the bash tool's own ``interrupted``
path emits ``<error>Command was aborted before completion</error>`` in the
tool_result content. The model reads this as a generic command failure
and, on the next turn (e.g. "please resume"), tends to retry the command
as if it had hit a transient bug — exactly what the user does NOT want.

The TS reference solves this in
``typescript/src/services/tools/StreamingToolExecutor.ts:153-205`` by
overriding the tool_result with ``REJECT_MESSAGE`` whenever the abort
reason is ``user_interrupted``. The Python production REPL bypasses
``StreamingToolExecutor`` and runs through ``query._dispatch_single_tool``
directly, so this file pins the same override into that production path.

Tests cover:

* Pre-tool gate — ESC fires before the dispatch begins.
* Post-tool override — ESC fires while the tool is running and the tool
  returns its own ``interrupted`` payload.
* ``AbortError`` raised by the tool — grep/glob style.
* ``sibling_error`` reason — must NOT mask the real failure with
  ``REJECT_MESSAGE`` (the cascade indicates a real parallel-tool error).
* Normal completion — no abort, no override.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from src.query.query import (
    _build_user_cancelled_result,
    _dispatch_single_tool,
    _is_user_cancelled_abort,
)
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolResult
from src.tool_system.registry import ToolRegistry
from src.tool_system.build_tool import build_tool
from src.types.content_blocks import ToolResultBlock, ToolUseBlock
from src.types.messages import REJECT_MESSAGE
from src.utils.abort_controller import AbortController, AbortError


def _make_ctx(workspace: Path) -> ToolContext:
    ctx = ToolContext(workspace_root=workspace)
    ctx.abort_controller = AbortController()
    return ctx


def _extract_tool_result(msg: Any) -> ToolResultBlock:
    # _dispatch_single_tool returns (primary: UserMessage, extras: list[UserMessage]).
    # Accept either the tuple form or a bare UserMessage for back-compat.
    if isinstance(msg, tuple):
        msg = msg[0]
    assert isinstance(msg.content, list)
    assert len(msg.content) == 1
    block = msg.content[0]
    assert isinstance(block, ToolResultBlock)
    return block


def test_is_user_cancelled_abort_false_when_signal_not_aborted(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    assert _is_user_cancelled_abort(ctx) is False


def test_is_user_cancelled_abort_true_on_user_interrupt(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    ctx.abort_controller.abort("user_interrupt")
    assert _is_user_cancelled_abort(ctx) is True


def test_is_user_cancelled_abort_false_on_sibling_error(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    ctx.abort_controller.abort("sibling_error")
    assert _is_user_cancelled_abort(ctx) is False


def test_build_user_cancelled_result_uses_reject_message() -> None:
    msg = _build_user_cancelled_result("call_42")
    block = _extract_tool_result(msg)
    assert block.tool_use_id == "call_42"
    assert block.content == REJECT_MESSAGE
    assert block.is_error is True


def test_dispatch_pre_tool_abort_returns_reject_message(tmp_path: Path) -> None:
    """Pre-tool gate: ESC trips BEFORE the dispatch starts.

    The registry must NOT be invoked (the user has already said stop) and
    the synthetic ``REJECT_MESSAGE`` must come out of the gate instead.
    """
    ctx = _make_ctx(tmp_path)
    ctx.abort_controller.abort("user_interrupt")

    registry = MagicMock()
    registry.dispatch = MagicMock(
        side_effect=AssertionError(
            "registry must not be hit when abort is already tripped"
        )
    )

    block = ToolUseBlock(id="call_1", name="Bash", input={"command": "ls"})
    result = _dispatch_single_tool(block, registry, ctx, tools=None)

    tool_result = _extract_tool_result(result)
    assert tool_result.content == REJECT_MESSAGE
    assert tool_result.is_error is True
    registry.dispatch.assert_not_called()


def test_dispatch_post_tool_abort_overrides_bash_interrupted_output(
    tmp_path: Path,
) -> None:
    """Post-tool override: bash returns the ``interrupted`` payload AND
    the abort is set when the result lands. The override fires so the
    model sees ``REJECT_MESSAGE`` instead of the bash tool's own
    ``<error>Command was aborted before completion</error>`` string.
    """
    ctx = _make_ctx(tmp_path)

    # Mirror the bash tool's interrupted return path (bash_tool.py:324-339).
    bash_output = {
        "cwd": str(tmp_path),
        "exit_code": -1,
        "stdout": "",
        "stderr": "",
        "interrupted": True,
    }

    def _call(_input: dict[str, Any], context: ToolContext) -> ToolResult:
        # Simulate ESC firing mid-run: the bash supervisor would have
        # observed the abort, killed the subprocess, and returned the
        # interrupted payload by this point.
        context.abort_controller.abort("user_interrupt")
        return ToolResult(
            name="Bash", output=bash_output, is_error=True,
        )

    bash_tool = build_tool(
        name="Bash",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=_call,
        prompt=lambda: "Bash",
        description=lambda _i: "shell",
    )
    registry = ToolRegistry()
    registry.register(bash_tool)

    block = ToolUseBlock(id="call_99", name="Bash", input={"command": "npm install"})
    result = _dispatch_single_tool(block, registry, ctx, tools=[bash_tool])

    tool_result = _extract_tool_result(result)
    assert tool_result.content == REJECT_MESSAGE, (
        "post-tool override must replace bash's interrupted payload with "
        "REJECT_MESSAGE so the next-turn resume sees a clear 'user "
        "rejected' signal"
    )
    assert "<error>Command was aborted before completion</error>" not in (
        tool_result.content
    )
    assert tool_result.is_error is True


def test_dispatch_tool_abort_error_returns_reject_message(tmp_path: Path) -> None:
    """A tool that raises ``AbortError`` (grep/glob via the ripgrep guard)
    AND has the abort signal tripped must funnel into ``REJECT_MESSAGE``.
    Without this branch the bare ``except Exception`` would stringify
    the error as a generic ``Error: ...`` payload and the resume turn
    would still look like a transient bug to the model.
    """
    ctx = _make_ctx(tmp_path)

    def _call(_input: dict[str, Any], context: ToolContext) -> ToolResult:
        context.abort_controller.abort("user_interrupt")
        raise AbortError("user_interrupt")

    tool = build_tool(
        name="Grep",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=_call,
        prompt=lambda: "Grep",
        description=lambda _i: "search",
    )
    registry = ToolRegistry()
    registry.register(tool)

    block = ToolUseBlock(id="call_5", name="Grep", input={"pattern": "TODO"})
    result = _dispatch_single_tool(block, registry, ctx, tools=[tool])

    tool_result = _extract_tool_result(result)
    assert tool_result.content == REJECT_MESSAGE
    assert tool_result.is_error is True


def test_dispatch_abort_error_without_signal_aborted_does_not_use_reject_message(
    tmp_path: Path,
) -> None:
    """Defensive: if a future tool raises ``AbortError`` WITHOUT having
    tripped the abort signal first (e.g. repurposed for its own internal
    cancellation), the dispatch must NOT silently relabel it as a user
    rejection — the user has no idea anything went wrong otherwise.

    Today every Python call site that raises ``AbortError`` does so only
    when the signal is already aborted; this test pins the contract so a
    future regression where that convention drifts is caught loudly.
    """
    ctx = _make_ctx(tmp_path)
    # Note: NO ``ctx.abort_controller.abort(...)`` — the signal stays clean.

    def _call(_input: dict[str, Any], _context: ToolContext) -> ToolResult:
        raise AbortError("internal cancellation by tool, not user")

    tool = build_tool(
        name="OddTool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=_call,
        prompt=lambda: "OddTool",
        description=lambda _i: "odd",
    )
    registry = ToolRegistry()
    registry.register(tool)

    block = ToolUseBlock(id="call_6", name="OddTool", input={})
    result = _dispatch_single_tool(block, registry, ctx, tools=[tool])

    tool_result = _extract_tool_result(result)
    assert tool_result.content != REJECT_MESSAGE, (
        "AbortError without an aborted signal must NOT be relabelled as "
        "user rejection — the cancellation came from the tool, not ESC"
    )
    assert tool_result.is_error is True
    assert "Tool execution aborted" in str(tool_result.content)
    assert "internal cancellation by tool, not user" in str(tool_result.content)


def test_sibling_error_does_not_override_with_reject_message(tmp_path: Path) -> None:
    """``sibling_error`` is the streaming-executor's parallel-tool
    cascade reason. The tool that actually failed must keep its real
    error payload so the user (and the model) can see what broke —
    relabelling it as "user rejected" would mask a real bug.

    Test setup: signal aborted with reason ``sibling_error``; the tool
    returns a recognizable error payload. The dispatch must NOT route
    through ``_build_user_cancelled_result`` — the tool's real output
    must come through.
    """
    ctx = _make_ctx(tmp_path)
    ctx.abort_controller.abort("sibling_error")

    sentinel = "real failure: parallel tool returned exit 2"

    def _call(_input: dict[str, Any], _context: ToolContext) -> ToolResult:
        return ToolResult(name="Read", output=sentinel, is_error=True)

    tool = build_tool(
        name="Read",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=_call,
        prompt=lambda: "Read",
        description=lambda _i: "read",
    )
    registry = ToolRegistry()
    registry.register(tool)

    block = ToolUseBlock(id="call_2", name="Read", input={"file_path": "x"})
    result = _dispatch_single_tool(block, registry, ctx, tools=[tool])

    tool_result = _extract_tool_result(result)
    assert tool_result.content != REJECT_MESSAGE, (
        "sibling_error must NOT be relabelled as 'user rejected' — the "
        "model needs the underlying parallel-tool failure to diagnose "
        "what went wrong"
    )
    assert sentinel in str(tool_result.content), (
        "the real tool failure payload must reach the model unchanged"
    )
    assert tool_result.is_error is True


def test_dispatch_normal_completion_does_not_override(tmp_path: Path) -> None:
    """Sanity check: a tool that completes successfully without any abort
    must NOT have its result rewritten to ``REJECT_MESSAGE``.
    """
    ctx = _make_ctx(tmp_path)

    def _call(_input: dict[str, Any], _context: ToolContext) -> ToolResult:
        return ToolResult(name="Read", output="hello world", is_error=False)

    tool = build_tool(
        name="Read",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=_call,
        prompt=lambda: "Read",
        description=lambda _i: "read",
    )
    registry = ToolRegistry()
    registry.register(tool)

    block = ToolUseBlock(id="call_7", name="Read", input={"file_path": "x"})
    result = _dispatch_single_tool(block, registry, ctx, tools=[tool])

    tool_result = _extract_tool_result(result)
    assert tool_result.content != REJECT_MESSAGE
    assert "hello world" in str(tool_result.content)
    assert tool_result.is_error is False


def test_dispatch_post_tool_abort_after_normal_completion(tmp_path: Path) -> None:
    """A tool that finishes successfully but the abort trips before the
    result is returned to the caller must still get the override —
    mirrors the TS per-iteration check at
    ``StreamingToolExecutor.ts:335`` which fires BEFORE pushing the
    update, regardless of whether the update was an error.
    """
    ctx = _make_ctx(tmp_path)

    def _call(_input: dict[str, Any], context: ToolContext) -> ToolResult:
        # The tool finished cleanly, then ESC fires before the dispatch
        # function gets to package the result up for the model.
        context.abort_controller.abort("user_interrupt")
        return ToolResult(name="Read", output="ok", is_error=False)

    tool = build_tool(
        name="Read",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=_call,
        prompt=lambda: "Read",
        description=lambda _i: "read",
    )
    registry = ToolRegistry()
    registry.register(tool)

    block = ToolUseBlock(id="call_9", name="Read", input={"file_path": "x"})
    result = _dispatch_single_tool(block, registry, ctx, tools=[tool])

    tool_result = _extract_tool_result(result)
    assert tool_result.content == REJECT_MESSAGE
    assert tool_result.is_error is True


def test_dispatch_unrelated_exception_with_abort_returns_reject_message(
    tmp_path: Path,
) -> None:
    """A late abort that races a tool exception: the user pressed ESC
    AND the tool also raised a non-AbortError. The user's intent wins —
    REJECT_MESSAGE is the correct framing.
    """
    ctx = _make_ctx(tmp_path)

    def _call(_input: dict[str, Any], context: ToolContext) -> ToolResult:
        context.abort_controller.abort("user_interrupt")
        raise RuntimeError("unrelated bug")

    tool = build_tool(
        name="Flaky",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=_call,
        prompt=lambda: "Flaky",
        description=lambda _i: "flaky",
    )
    registry = ToolRegistry()
    registry.register(tool)

    block = ToolUseBlock(id="call_3", name="Flaky", input={})
    result = _dispatch_single_tool(block, registry, ctx, tools=[tool])

    tool_result = _extract_tool_result(result)
    assert tool_result.content == REJECT_MESSAGE


def test_dispatch_unrelated_exception_no_abort_falls_through_to_error(
    tmp_path: Path,
) -> None:
    """When no abort fires, a plain exception still produces the legacy
    ``Error: ...`` stringification — REJECT_MESSAGE must not leak into
    the non-cancel error path.
    """
    ctx = _make_ctx(tmp_path)

    def _call(_input: dict[str, Any], _context: ToolContext) -> ToolResult:
        raise RuntimeError("boom")

    tool = build_tool(
        name="Boom",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=_call,
        prompt=lambda: "Boom",
        description=lambda _i: "boom",
    )
    registry = ToolRegistry()
    registry.register(tool)

    block = ToolUseBlock(id="call_4", name="Boom", input={})
    result = _dispatch_single_tool(block, registry, ctx, tools=[tool])

    tool_result = _extract_tool_result(result)
    assert tool_result.content != REJECT_MESSAGE
    assert "Error" in str(tool_result.content)
    assert "boom" in str(tool_result.content)
    assert tool_result.is_error is True

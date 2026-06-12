"""ESC / user-cancel contract pins — REJECT_MESSAGE dispatch semantics.

Originally pinned the query.py slim lane (`_dispatch_single_tool`); ch07
PR-1 retired that lane, so these pins now arbitrate the UNIFIED lane
(`run_tool_use` / `_check_permissions_and_call_tool`), whose user-cancel
classification + REJECT_MESSAGE overrides were ported verbatim. The
contract (unchanged): pre-tool gate, post-tool override, AbortError
funnel — all must surface REJECT_MESSAGE for user-initiated aborts so the
resume turn sees an unambiguous "user rejected" signal (TS
StreamingToolExecutor.ts:153-205), and tool_use/tool_result pairing must
stay intact.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.services.tool_execution.tool_execution import (
    _build_user_cancelled_message,
    _is_user_cancelled_abort,
    run_tool_use,
)
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.types.content_blocks import ToolResultBlock, ToolUseBlock
from src.types.messages import AssistantMessage, REJECT_MESSAGE
from src.utils.abort_controller import AbortController, AbortError


def _make_ctx(workspace: Path, tools: list | None = None) -> ToolContext:
    ctx = ToolContext(
        workspace_root=workspace,
        options=ToolUseOptions(tools=tools or []),
    )
    ctx.abort_controller = AbortController()
    ctx.permission_context.mode = "bypassPermissions"
    return ctx


def _allow(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {"behavior": "allow"}


def _run_tool(ctx: ToolContext, tool, name: str, input_: dict, tid: str):
    block = ToolUseBlock(id=tid, name=name, input=input_)

    async def drive():
        updates = []
        async for u in run_tool_use(
            block, AssistantMessage(content="t"), _allow, ctx
        ):
            updates.append(u)
        return updates

    return asyncio.run(drive())


def _extract_tool_result(updates: list) -> Any:
    msgs = [getattr(u, "message", u) for u in updates]
    blocks = []
    for m in msgs:
        content = getattr(m, "content", None)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, ToolResultBlock):
                    blocks.append(b)
                elif isinstance(b, dict) and b.get("type") == "tool_result":
                    blocks.append(ToolResultBlock(
                        tool_use_id=b.get("tool_use_id", ""),
                        content=b.get("content", ""),
                        is_error=bool(b.get("is_error")),
                    ))
    assert len(blocks) == 1, f"expected exactly one tool_result, got {blocks}"
    return blocks[0]


def _stub(name: str, call):
    return build_tool(
        name=name,
        input_schema={
            "type": "object", "properties": {}, "additionalProperties": True,
        },
        call=call,
        prompt=name,
        description=name,
    )


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


def test_is_user_cancelled_abort_false_on_streaming_fallback(tmp_path: Path) -> None:
    # ch07: the discarded streaming executor's reason is not a user
    # rejection either.
    ctx = _make_ctx(tmp_path)
    ctx.abort_controller.abort("streaming_fallback")
    assert _is_user_cancelled_abort(ctx) is False


def test_build_user_cancelled_message_uses_reject_message() -> None:
    msg = _build_user_cancelled_message("call_42")
    block = msg.content[0]
    assert isinstance(block, ToolResultBlock)
    assert block.tool_use_id == "call_42"
    assert block.content == REJECT_MESSAGE
    assert block.is_error is True


def test_pre_tool_abort_returns_reject_message(tmp_path: Path) -> None:
    """Pre-tool gate: ESC trips BEFORE dispatch — the tool must NOT run
    and the synthetic REJECT_MESSAGE must come out of the gate."""
    ran: list = []

    def _call(_input: dict[str, Any], _context: ToolContext) -> ToolResult:
        ran.append(True)
        return ToolResult(name="Bash", output="should not happen")

    tool = _stub("Bash", _call)
    ctx = _make_ctx(tmp_path, [tool])
    ctx.abort_controller.abort("user_interrupt")

    updates = _run_tool(ctx, tool, "Bash", {"command": "ls"}, "call_1")
    tool_result = _extract_tool_result(updates)
    assert tool_result.content == REJECT_MESSAGE
    assert tool_result.is_error is True
    assert not ran


def test_post_tool_abort_overrides_bash_interrupted_output(tmp_path: Path) -> None:
    """Post-tool override: bash returns its ``interrupted`` payload AND
    the abort is set when the result lands — the model must see
    REJECT_MESSAGE, not bash's generic aborted string."""
    bash_output = {
        "cwd": str(tmp_path),
        "exit_code": -1,
        "stdout": "",
        "stderr": "",
        "interrupted": True,
    }

    def _call(_input: dict[str, Any], context: ToolContext) -> ToolResult:
        context.abort_controller.abort("user_interrupt")
        return ToolResult(name="Bash", output=bash_output, is_error=True)

    tool = _stub("Bash", _call)
    ctx = _make_ctx(tmp_path, [tool])
    updates = _run_tool(ctx, tool, "Bash", {"command": "npm install"}, "call_99")

    tool_result = _extract_tool_result(updates)
    assert tool_result.content == REJECT_MESSAGE, (
        "post-tool override must replace bash's interrupted payload with "
        "REJECT_MESSAGE so the next-turn resume sees a clear 'user "
        "rejected' signal"
    )
    assert "<error>Command was aborted before completion</error>" not in str(
        tool_result.content
    )
    assert tool_result.is_error is True


def test_tool_abort_error_returns_reject_message(tmp_path: Path) -> None:
    """A tool that raises AbortError with the signal tripped must funnel
    into REJECT_MESSAGE (not a generic Error payload)."""

    def _call(_input: dict[str, Any], context: ToolContext) -> ToolResult:
        context.abort_controller.abort("user_interrupt")
        raise AbortError("user_interrupt")

    tool = _stub("Grep", _call)
    ctx = _make_ctx(tmp_path, [tool])
    updates = _run_tool(ctx, tool, "Grep", {"pattern": "TODO"}, "call_5")

    tool_result = _extract_tool_result(updates)
    assert tool_result.content == REJECT_MESSAGE
    assert tool_result.is_error is True


def test_abort_error_without_signal_does_not_use_reject_message(
    tmp_path: Path,
) -> None:
    """Defensive: AbortError WITHOUT a tripped signal must NOT be
    relabelled as a user rejection — and the dual contract requires the
    signal be tripped (no follow-up API turn) with a PAIRED tool_result."""

    def _call(_input: dict[str, Any], _context: ToolContext) -> ToolResult:
        raise AbortError("internal cancellation by tool, not user")

    tool = _stub("OddTool", _call)
    ctx = _make_ctx(tmp_path, [tool])
    updates = _run_tool(ctx, tool, "OddTool", {}, "call_7")

    tool_result = _extract_tool_result(updates)
    assert tool_result.content != REJECT_MESSAGE
    assert "aborted" in str(tool_result.content).lower()
    assert tool_result.is_error is True
    # The loop's post-tools abort gate must see a tripped signal so no
    # follow-up API turn happens.
    assert ctx.abort_controller.signal.aborted
    assert ctx.abort_controller.signal.reason == "tool_raised_abort_error"

"""Thrown-tool-error wire format parity — port of TS formatError.

The original sends thrown ``call()`` errors RAW: ``content:
formatError(error)`` with ``is_error: true`` and NO ``<tool_use_error>``
wrapping (toolExecution.ts:1633+1674). The tags are reserved for
pre-execution failures (no-such-tool :391, InputValidationError :726,
validate_input :783), which we tag identically. The old ``_format_error``
wrapped thrown errors too, which (a) diverged the model-facing bytes and
(b) leaked ``Error: <tool_use_error>path is not a file: …</tool_use_error>``
into the TUI transcript.

Because ``role=tool`` messages have no ``is_error`` field on the OpenAI
wire, converters must carry the error signal in text: ``Error: `` prefix,
mirroring TS convertToolResultContent (openaiShim.ts:309/:349/:356). Without
it, de-tagging would have erased the failure signal for OpenAI-wire models.

Also pins the Read-on-directory message: EISDIR wording, byte-identical to
TS readFileInRange.ts:89-92.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.providers.openai_compatible import _convert_anthropic_messages_to_openai
from src.providers.openai_responses import convert_messages_to_responses_input
from src.services.tool_execution.tool_execution import _format_error, run_tool_use
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.errors import ToolInputError
from src.tool_system.tools.read import _read_call
from src.types.content_blocks import ToolResultBlock, ToolUseBlock
from src.types.messages import INTERRUPT_MESSAGE_FOR_TOOL_USE, AssistantMessage
from src.utils.abort_controller import AbortController, AbortError


# ---------------------------------------------------------------------------
# _format_error — unit (toolErrors.ts:5-43)
# ---------------------------------------------------------------------------

class TestFormatError(unittest.TestCase):
    def test_plain_exception_is_raw_untagged(self) -> None:
        out = _format_error(ValueError("path is not a file: /x/src"))
        self.assertEqual(out, "path is not a file: /x/src")
        self.assertNotIn("<tool_use_error>", out)

    def test_stderr_stdout_string_attrs_join_as_parts(self) -> None:
        # getErrorParts (toolErrors.ts:26): message, stderr, stdout —
        # subprocess.CalledProcessError carries both attributes.
        err = RuntimeError("exit 1")
        err.stderr = "boom to stderr"
        err.stdout = "partial stdout"
        self.assertEqual(
            _format_error(err), "exit 1\nboom to stderr\npartial stdout"
        )

    def test_non_string_stderr_excluded(self) -> None:
        err = RuntimeError("exit 1")
        err.stderr = b"bytes ignored"  # TS: typeof === 'string' gate
        self.assertEqual(_format_error(err), "exit 1")

    def test_empty_message_falls_back(self) -> None:
        self.assertEqual(_format_error(ValueError()), "Command failed with no output")

    def test_over_40kb_middle_truncated(self) -> None:
        msg = "x" * 50_000
        out = _format_error(ValueError(msg))
        self.assertIn("... [10000 characters truncated] ...", out)
        self.assertTrue(out.startswith("x" * 100))
        self.assertTrue(out.endswith("x" * 100))
        # 20_000 head + 20_000 tail + the marker line
        self.assertLess(len(out), 41_000)

    def test_abort_error_uses_message_or_interrupt_constant(self) -> None:
        self.assertEqual(_format_error(AbortError("stop reason")), "stop reason")
        # Python AbortError defaults reason="aborted", so the TS
        # `error.message || INTERRUPT_MESSAGE_FOR_TOOL_USE` fallback only
        # fires for an explicitly empty reason.
        self.assertEqual(
            _format_error(AbortError("")), INTERRUPT_MESSAGE_FOR_TOOL_USE
        )


# ---------------------------------------------------------------------------
# Thrown tool error through the pipeline — content untagged on the wire
# ---------------------------------------------------------------------------

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


def _run_tool(ctx: ToolContext, name: str, tid: str) -> list:
    block = ToolUseBlock(id=tid, name=name, input={})

    async def drive():
        updates = []
        async for u in run_tool_use(
            block, AssistantMessage(content="t"), _allow, ctx
        ):
            updates.append(u)
        return updates

    return asyncio.run(drive())


def _sole_tool_result(updates: list) -> tuple[Any, Any]:
    """(tool_result block, carrying message) — exactly one expected."""
    found = []
    for u in updates:
        m = getattr(u, "message", u)
        content = getattr(m, "content", None)
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, ToolResultBlock):
                found.append((b, m))
            elif isinstance(b, dict) and b.get("type") == "tool_result":
                found.append((
                    ToolResultBlock(
                        tool_use_id=b.get("tool_use_id", ""),
                        content=b.get("content", ""),
                        is_error=bool(b.get("is_error")),
                    ),
                    m,
                ))
    assert len(found) == 1, f"expected exactly one tool_result, got {found}"
    return found[0]


class TestThrownToolErrorWireFormat(unittest.TestCase):
    def test_thrown_error_content_is_raw_with_is_error(self) -> None:
        def boom(_input, _ctx):
            raise RuntimeError("kaboom with details")

        with tempfile.TemporaryDirectory() as tmp:
            tool = _stub("Boom", boom)
            ctx = _make_ctx(Path(tmp), tools=[tool])
            block, msg = _sole_tool_result(_run_tool(ctx, "Boom", "t1"))

        self.assertTrue(block.is_error)
        self.assertEqual(block.content, "kaboom with details")
        # toolUseResult mirrors TS toolExecution.ts:1674: `Error: ${content}`.
        self.assertEqual(
            getattr(msg, "toolUseResult", None), "Error: kaboom with details"
        )

    def test_validation_failure_keeps_tool_use_error_tags(self) -> None:
        """Pre-execution failures stay tagged (TS toolExecution.ts:726)."""
        def ok(_input, _ctx):  # pragma: no cover - never reached
            return "unreachable"

        tool = build_tool(
            name="Strict",
            input_schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
                "additionalProperties": False,
            },
            call=ok,
            prompt="Strict",
            description="Strict",
        )
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _make_ctx(Path(tmp), tools=[tool])
            block, _ = _sole_tool_result(_run_tool(ctx, "Strict", "t2"))

        self.assertTrue(block.is_error)
        self.assertTrue(block.content.startswith("<tool_use_error>InputValidationError: "))
        self.assertTrue(block.content.endswith("</tool_use_error>"))


# ---------------------------------------------------------------------------
# Read on a directory — EISDIR parity (readFileInRange.ts:89-92)
# ---------------------------------------------------------------------------

class TestReadDirectoryEisdir(unittest.TestCase):
    def test_directory_raises_eisdir_wording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ToolContext(workspace_root=Path(tmp))
            # ensure_readable_path resolves symlinks (macOS /var → /private/var),
            # so the message names the resolved path.
            target = (Path(tmp) / "src").resolve()
            target.mkdir()
            with self.assertRaises(ToolInputError) as caught:
                _read_call({"file_path": str(target)}, ctx)
        self.assertEqual(
            str(caught.exception),
            f"EISDIR: illegal operation on a directory, read '{target}'",
        )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "mkfifo not available")
    def test_non_directory_non_file_keeps_generic_message(self) -> None:
        # TS streams FIFOs/devices; we refuse — deliberate divergence, and
        # those must NOT masquerade as EISDIR.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ToolContext(workspace_root=Path(tmp))
            fifo = (Path(tmp) / "pipe").resolve()
            os.mkfifo(fifo)
            with self.assertRaises(ToolInputError) as caught:
                _read_call({"file_path": str(fifo)}, ctx)
        self.assertEqual(str(caught.exception), f"path is not a file: {fifo}")


# ---------------------------------------------------------------------------
# Converters — is_error rides the text as an ``Error: `` prefix
# ---------------------------------------------------------------------------

class TestOpenAICompatErrorPrefix(unittest.TestCase):
    def _convert(self, content: str, is_error: bool) -> list:
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_a", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_a",
                    "content": content,
                    "is_error": is_error,
                },
            ]},
        ]
        return _convert_anthropic_messages_to_openai(messages)

    def test_error_result_prefixed(self) -> None:
        out = self._convert(
            "EISDIR: illegal operation on a directory, read '/x/src'", True
        )
        self.assertEqual(out[1]["role"], "tool")
        self.assertEqual(
            out[1]["content"],
            "Error: EISDIR: illegal operation on a directory, read '/x/src'",
        )

    def test_success_result_unprefixed(self) -> None:
        out = self._convert("1\thello\n", False)
        self.assertEqual(out[1]["content"], "1\thello\n")

    def test_tagged_validation_error_prefixed_unconditionally(self) -> None:
        # TS applies the prefix to every is_error emission — even content
        # that already carries the tag envelope (openaiShim.ts:309).
        out = self._convert("<tool_use_error>InputValidationError: bad</tool_use_error>", True)
        self.assertEqual(
            out[1]["content"],
            "Error: <tool_use_error>InputValidationError: bad</tool_use_error>",
        )


class TestResponsesErrorPrefix(unittest.TestCase):
    def _convert(self, content: str, is_error: bool) -> list:
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_a", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_a",
                    "content": content,
                    "is_error": is_error,
                },
            ]},
        ]
        items, _instructions = convert_messages_to_responses_input(messages)
        return [i for i in items if i.get("type") == "function_call_output"]

    def test_error_output_prefixed(self) -> None:
        outputs = self._convert("boom", True)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["output"], "Error: boom")

    def test_success_output_unprefixed(self) -> None:
        outputs = self._convert("fine", False)
        self.assertEqual(outputs[0]["output"], "fine")


if __name__ == "__main__":
    unittest.main()

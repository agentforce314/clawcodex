"""Tool-input validation parity: semantic coercion + formatZodValidationError text.

Regression suite for the live failure ``InputValidationError: Grep.n:
unexpected field`` — the model emitted ``n`` for the dash-flag ``-n``. The
rejection itself is parity-correct (the original's ``z.strictObject`` refuses
unknown keys too), but the incident exposed two gaps in the same code path:

* Error text: the original replies with ``formatZodValidationError`` prose
  ("Grep failed due to the following issue:\\nAn unexpected parameter `n` was
  provided", typescript/src/utils/toolErrors.ts:68) — a materially better
  recovery hint than the port's old ``Grep.n: unexpected field``.
* Semantic coercion: the original's schemas wrap boolean/number fields in
  ``semanticBoolean``/``semanticNumber`` preprocess steps, so quoted scalars
  (``"-n": "true"``, ``"head_limit": "30"``) validate and coerce. The port
  validated the raw JSON schema first, hard-rejecting input the original
  accepts.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import pytest

from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.errors import ToolInputError
from src.tool_system.protocol import ToolCall, ToolResult
from src.tool_system.registry import ToolRegistry
from src.tool_system.schema_validation import (
    semantic_coerce,
    validate_json_schema,
    validate_tool_input,
)
from src.tool_system.tools.grep import GrepTool


# --------------------------------------------------------------------------- #
# semantic_coerce — mirrors semanticBoolean.ts / semanticNumber.ts
# --------------------------------------------------------------------------- #

_BOOL = {"type": "boolean"}
_NUM = {"type": "number"}
_INT = {"type": "integer"}


class TestSemanticCoerce(unittest.TestCase):
    def test_boolean_literals_coerce(self):
        self.assertIs(semantic_coerce("true", _BOOL), True)
        self.assertIs(semantic_coerce("false", _BOOL), False)

    def test_boolean_non_literals_pass_through(self):
        # semanticBoolean coerces ONLY "true"/"false" — JS-truthiness would
        # turn "false" into True, and "1"/"yes"/"TRUE" are rejected upstream.
        for v in ("TRUE", "False", "1", "0", "yes", "no", ""):
            self.assertIs(semantic_coerce(v, _BOOL), v)

    def test_number_literals_coerce(self):
        self.assertEqual(semantic_coerce("30", _NUM), 30)
        self.assertIsInstance(semantic_coerce("30", _NUM), int)
        self.assertEqual(semantic_coerce("3.14", _NUM), 3.14)
        self.assertEqual(semantic_coerce("-5", _NUM), -5)
        self.assertEqual(semantic_coerce("007", _NUM), 7)
        self.assertEqual(semantic_coerce("5", _INT), 5)

    def test_number_non_literals_pass_through(self):
        # /^-?\d+(\.\d+)?$/ — scientific notation, bare dots, whitespace and
        # garbage all fall through to the type check, exactly like the
        # original's regex gate.
        for v in ("1e5", "abc", ".5", "1.", "30 ", " 30", "+5", "NaN", "Infinity", ""):
            self.assertIs(semantic_coerce(v, _NUM), v)

    def test_number_overflowing_js_range_passes_through(self):
        # Number("9" * 400) is Infinity in JS, so semanticNumber's
        # Number.isFinite gate refuses it — mirror that rather than minting
        # a Python bigint the original would have rejected.
        huge = "9" * 400
        self.assertIs(semantic_coerce(huge, _NUM), huge)

    def test_non_boolean_number_types_untouched(self):
        self.assertIs(semantic_coerce("true", {"type": "string"}), "true")
        self.assertEqual(semantic_coerce(True, _BOOL), True)
        self.assertEqual(semantic_coerce(30, _NUM), 30)

    def test_union_schemas_never_coerce(self):
        # ConfigTool.value is z.union([string, boolean, number]) with no
        # semantic wrapper — "true" must stay a string there.
        union = {"anyOf": [{"type": "string"}, {"type": "boolean"}]}
        self.assertIs(semantic_coerce("true", union), "true")
        one_of = {"oneOf": [{"type": "boolean"}, {"type": "number"}]}
        self.assertIs(semantic_coerce("30", one_of), "30")

    def test_object_properties_coerce_copy_on_write(self):
        schema = {
            "type": "object",
            "properties": {"flag": _BOOL, "limit": _NUM, "name": {"type": "string"}},
        }
        original = {"flag": "true", "limit": "30", "name": "x"}
        coerced = semantic_coerce(original, schema)
        self.assertEqual(coerced, {"flag": True, "limit": 30, "name": "x"})
        # The input is never mutated — the assistant message's recorded
        # tool_use block may share this dict.
        self.assertEqual(original, {"flag": "true", "limit": "30", "name": "x"})
        self.assertIsNot(coerced, original)

    def test_object_without_coercions_returns_same_object(self):
        schema = {"type": "object", "properties": {"flag": _BOOL}}
        original = {"flag": True}
        self.assertIs(semantic_coerce(original, schema), original)

    def test_unknown_keys_left_alone(self):
        schema = {"type": "object", "properties": {"flag": _BOOL}, "additionalProperties": False}
        original = {"stray": "true"}
        self.assertIs(semantic_coerce(original, schema), original)

    def test_nested_array_items_coerce(self):
        schema = {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"done": _BOOL}},
                },
            },
        }
        original = {"todos": [{"done": "true"}, {"done": False}]}
        coerced = semantic_coerce(original, schema)
        self.assertEqual(coerced, {"todos": [{"done": True}, {"done": False}]})
        self.assertEqual(original["todos"][0]["done"], "true")


# --------------------------------------------------------------------------- #
# validate_tool_input — message parity with formatZodValidationError
# --------------------------------------------------------------------------- #

class TestValidateToolInputMessages(unittest.TestCase):
    def _error(self, tool_input: dict) -> str:
        with pytest.raises(ToolInputError) as exc_info:
            validate_tool_input("Grep", tool_input, GrepTool.input_schema)
        return str(exc_info.value)

    def test_unexpected_parameter_exact_live_repro(self):
        # The live failure: the model sent ``n`` instead of ``-n``. Original
        # reply for the same input, byte for byte.
        msg = self._error({"pattern": "TODO|FIXME", "n": True})
        self.assertEqual(
            msg,
            "Grep failed due to the following issue:\n"
            "An unexpected parameter `n` was provided",
        )

    def test_missing_required_parameter(self):
        msg = self._error({})
        self.assertEqual(
            msg,
            "Grep failed due to the following issue:\n"
            "The required parameter `pattern` is missing",
        )

    def test_type_mismatch_uses_js_type_names(self):
        # Python's int must render as JS "number".
        msg = self._error({"pattern": 5})
        self.assertEqual(
            msg,
            "Grep failed due to the following issue:\n"
            "The parameter `pattern` type is expected as `string` but provided as `number`",
        )

    def test_multiple_issues_grouped_and_plural(self):
        # Order mirrors the original: missing, then unexpected, then type
        # mismatches — regardless of input key order.
        msg = self._error({"-i": 3, "n": True})
        self.assertEqual(
            msg,
            "Grep failed due to the following issues:\n"
            "The required parameter `pattern` is missing\n"
            "An unexpected parameter `n` was provided\n"
            "The parameter `-i` type is expected as `boolean` but provided as `number`",
        )

    def test_uncategorized_issue_falls_back_to_generic_rendering(self):
        # Enum violations have no curated category (the original dumps zod's
        # raw error.message there); the port keeps its readable fallback.
        msg = self._error({"pattern": "x", "output_mode": "bogus"})
        self.assertIn("Grep.output_mode: expected one of", msg)
        self.assertNotIn("failed due to the following", msg)

    def test_semantic_strings_validate_and_coerce(self):
        # The sibling failure mode of the live repro — quoted scalars — must
        # validate exactly as the original's semantic wrappers allow.
        out = validate_tool_input(
            "Grep",
            {"pattern": "x", "-n": "true", "-i": "false", "head_limit": "30", "-B": "2"},
            GrepTool.input_schema,
        )
        self.assertEqual(
            out, {"pattern": "x", "-n": True, "-i": False, "head_limit": 30, "-B": 2},
        )

    def test_valid_input_returned_unchanged_same_object(self):
        tool_input = {"pattern": "x", "output_mode": "content"}
        self.assertIs(
            validate_tool_input("Grep", tool_input, GrepTool.input_schema), tool_input,
        )


# --------------------------------------------------------------------------- #
# validate_json_schema — generic callers keep the old rendering
# --------------------------------------------------------------------------- #

class TestGenericValidationUnchanged(unittest.TestCase):
    def test_unexpected_field_old_format(self):
        schema = {"type": "object", "additionalProperties": False, "properties": {}}
        with pytest.raises(ToolInputError) as exc_info:
            validate_json_schema({"n": 1}, schema, root_name="Grep")
        self.assertEqual(str(exc_info.value), "Grep.n: unexpected field")

    def test_missing_required_old_format(self):
        schema = {"type": "object", "required": ["pattern"], "properties": {}}
        with pytest.raises(ToolInputError) as exc_info:
            validate_json_schema({}, schema, root_name="output")
        self.assertEqual(str(exc_info.value), "output: missing required field 'pattern'")

    def test_no_semantic_coercion_on_generic_path(self):
        # Structured-output validation (workflow/structured.py) must stay
        # strict — only the tool-dispatch entrypoint coerces.
        schema = {"type": "object", "properties": {"flag": {"type": "boolean"}}}
        with pytest.raises(ToolInputError):
            validate_json_schema({"flag": "true"}, schema)


# --------------------------------------------------------------------------- #
# Dispatch integration — coerced input is what the pipeline carries forward
# --------------------------------------------------------------------------- #

def _capture_tool(received: dict):
    return build_tool(
        name="Stub",
        description="stub",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"flag": {"type": "boolean"}, "limit": {"type": "number"}},
        },
        call=lambda tool_input, context: (
            received.update(tool_input) or ToolResult(name="Stub", output={"ok": True})
        ),
    )


class TestRegistryDispatchCoercion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.received: dict = {}
        self.registry = ToolRegistry([_capture_tool(self.received)])
        self.ctx = ToolContext(workspace_root=Path(self.tmp.name).resolve())
        self.ctx.permission_context.mode = "bypassPermissions"

    def tearDown(self):
        self.tmp.cleanup()

    def test_call_receives_coerced_input_original_unmutated(self):
        original = {"flag": "true", "limit": "30"}
        result = self.registry.dispatch(
            ToolCall(name="Stub", input=original, tool_use_id="t1"), self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(self.received, {"flag": True, "limit": 30})
        self.assertEqual(original, {"flag": "true", "limit": "30"})

    def test_unknown_key_raises_ts_format_message(self):
        with pytest.raises(ToolInputError) as exc_info:
            self.registry.dispatch(
                ToolCall(name="Stub", input={"n": True}, tool_use_id="t2"), self.ctx,
            )
        self.assertEqual(
            str(exc_info.value),
            "Stub failed due to the following issue:\n"
            "An unexpected parameter `n` was provided",
        )


class _ToolUse:
    def __init__(self, name: str, input_: dict, id_: str = "toolu_val_1"):
        self.name = name
        self.input = input_
        self.id = id_


class TestRunToolUseValidation(unittest.TestCase):
    """The services lane (live agent loop) — the path the incident hit."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.received: dict = {}
        self.tool = _capture_tool(self.received)

    def tearDown(self):
        self.tmp.cleanup()

    def _execute(self, tool_input: dict) -> list[dict]:
        from src.services.tool_execution.tool_execution import run_tool_use
        from src.types.messages import AssistantMessage

        ctx = ToolContext(
            workspace_root=self.workspace,
            options=ToolUseOptions(tools=[self.tool]),
        )
        ctx.permission_context.mode = "bypassPermissions"

        async def drive():
            updates = []
            async for update in run_tool_use(
                _ToolUse("Stub", tool_input),
                AssistantMessage(content="using a tool"),
                lambda *_a, **_k: {"behavior": "allow"},
                ctx,
            ):
                updates.append(update)
            return updates

        updates = asyncio.run(drive())
        blocks = []
        for u in updates:
            msg = getattr(u, "message", u)
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        blocks.append(block)
                    elif hasattr(block, "tool_use_id") and hasattr(block, "content"):
                        # Success results arrive as ToolResultBlock dataclass
                        # instances — normalize like test_tool_pipeline_round3.
                        blocks.append({
                            "type": "tool_result",
                            "tool_use_id": block.tool_use_id,
                            "content": block.content,
                            "is_error": getattr(block, "is_error", False),
                        })
        return blocks

    def test_validation_error_wrapped_with_ts_format_text(self):
        blocks = self._execute({"n": True})
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0]["is_error"])
        self.assertEqual(
            blocks[0]["content"],
            "<tool_use_error>InputValidationError: "
            "Stub failed due to the following issue:\n"
            "An unexpected parameter `n` was provided</tool_use_error>",
        )

    def test_coerced_input_reaches_call(self):
        blocks = self._execute({"flag": "true", "limit": "30"})
        self.assertEqual(len(blocks), 1)
        self.assertFalse(blocks[0].get("is_error", False))
        self.assertEqual(self.received, {"flag": True, "limit": 30})


class TestSendMessageApproveFlag(unittest.TestCase):
    """``approve`` sits INSIDE the SendMessage union (semanticBoolean at
    SendMessageTool.ts:55,61), which the boundary coercion deliberately
    skips — the tool reads it with the same tolerance at runtime."""

    def test_quoted_literals_and_bools(self):
        from src.tool_system.tools.send_message import _approve_flag

        self.assertIs(_approve_flag({"approve": "false"}), False)
        self.assertIs(_approve_flag({"approve": "true"}), True)
        self.assertIs(_approve_flag({"approve": True}), True)
        self.assertIs(_approve_flag({"approve": False}), False)
        self.assertIs(_approve_flag({}), False)


if __name__ == "__main__":
    unittest.main()

"""Schema-validated structured output for ``agent(prompt, schema=...)``.

The clawcodex ``StructuredOutputTool`` is currently an unvalidated no-op whose
result never returns to a caller, so the workflow engine must own validation
itself. The pure pieces live here (and are unit-tested directly); the
production ``AgentRunner`` wires :class:`StructuredOutputCollector` into the
injected ``StructuredOutput`` tool given to a schema subagent.

Validation reuses the repository's only JSON-Schema validator,
``src.tool_system.schema_validation.validate_json_schema`` (raises
``ToolInputError`` on mismatch).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.errors import ToolInputError
from src.tool_system.protocol import ToolResult
from src.tool_system.schema_validation import validate_json_schema

from .constants import MAX_STRUCTURED_OUTPUT_RETRIES

#: The name the model is told to call. Matches the stock ``StructuredOutputTool``
#: so the per-call schema tool shadows it in the subagent's tool pool.
SYNTHETIC_OUTPUT_TOOL_NAME = "StructuredOutput"


def validate_structured(obj: Any, schema: Mapping[str, Any]) -> tuple[bool, Optional[str]]:
    """Validate ``obj`` against ``schema``; return ``(ok, error_message)``."""
    try:
        validate_json_schema(obj, schema, root_name="output")
        return True, None
    except ToolInputError as exc:
        return False, str(exc)


def _schema_types(schema: Mapping[str, Any]) -> tuple[str, ...]:
    declared = schema.get("type")
    if isinstance(declared, str):
        return (declared,)
    if isinstance(declared, list):
        return tuple(t for t in declared if isinstance(t, str))
    return ()


def coerce_to_schema(obj: Any, schema: Any) -> Any:
    """Lenient pre-pass for weak-model outputs (#282).

    Weak models (glm et al.) emit JSON types as strings — ``"42"``,
    ``"true"``, a JSON-encoded array — and burn every schema-repair
    retry on trivially coercible mismatches. Keyed strictly on what the
    schema *expects*: numeric/boolean strings coerce where the schema
    wants ``number``/``integer``/``boolean``; string values parse via
    ``json.loads`` where it wants ``array``/``object``; integral floats
    coerce where it wants ``integer``. Anything that doesn't cleanly
    coerce is returned unchanged so strict validation reports the real
    error. Never raises.

    Only ``type``/``properties``/``items`` are consulted —
    ``anyOf``/``oneOf`` union schemas get no coercion (values pass
    through to strict validation untouched).
    """
    if not isinstance(schema, Mapping):
        return obj
    types = _schema_types(schema)

    if isinstance(obj, str):
        # A string that already satisfies a union with "string" is left
        # alone (ajv coerceTypes parity: coerce only when the value
        # matches no declared type).
        if "string" in types:
            return obj
        text = obj.strip()
        if "boolean" in types and text.lower() in ("true", "false"):
            return text.lower() == "true"
        if "integer" in types:
            try:
                return int(text)
            except ValueError:
                # ajv coerceTypes parity: "3.0" -> 3 for integer
                # schemas, but only for finite, integral floats.
                try:
                    parsed = float(text)
                except ValueError:
                    pass
                else:
                    if math.isfinite(parsed) and parsed.is_integer():
                        return int(parsed)
        if "number" in types:
            try:
                parsed = float(text)
            except ValueError:
                pass
            else:
                # isfinite: "NaN"/"Infinity" parse as floats but are
                # not valid JSON numbers — let strict validation reject
                # the original string loudly instead.
                if math.isfinite(parsed):
                    return int(parsed) if parsed.is_integer() else parsed
        if ("array" in types or "object" in types) and text[:1] in ("[", "{"):
            try:
                decoded = json.loads(text)
            except ValueError:
                return obj
            if (isinstance(decoded, list) and "array" in types) or (
                isinstance(decoded, dict) and "object" in types
            ):
                return coerce_to_schema(decoded, schema)
        return obj

    # Bools never reach this branch (isinstance(True, float) is False)
    # and fall through every other rule untouched — True must never
    # become 1. inf/nan are excluded by is_integer().
    if (
        isinstance(obj, float)
        and "integer" in types
        and "number" not in types
        and obj.is_integer()
    ):
        return int(obj)

    if isinstance(obj, dict):
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            return {
                key: coerce_to_schema(value, properties.get(key))
                for key, value in obj.items()
            }
        return obj

    if isinstance(obj, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            return [coerce_to_schema(item, items) for item in obj]
        return obj

    return obj


@dataclass
class StructuredOutputCollector:
    """Accumulates a schema subagent's ``StructuredOutput`` emissions.

    The injected tool calls :meth:`offer` for each emission. A valid object is
    captured and ends the run; an invalid one returns the validation error (fed
    back to the model as a tool error so it retries) until the retry cap is hit.
    """

    schema: Mapping[str, Any]
    max_retries: int = MAX_STRUCTURED_OUTPUT_RETRIES
    attempts: int = 0
    value: Any = None
    succeeded: bool = False
    last_error: Optional[str] = None

    def offer(self, obj: Any) -> tuple[bool, Optional[str]]:
        """Validate one emission. Returns ``(accepted, error_message)``."""
        if self.succeeded:
            return True, None
        self.attempts += 1
        # Lenient pre-pass (#282): coerce string-typed scalars and
        # JSON-encoded containers toward what the schema expects before
        # strict validation, so weak-model outputs don't burn retries on
        # trivially fixable type mismatches.
        obj = coerce_to_schema(obj, self.schema)
        ok, error = validate_structured(obj, self.schema)
        if ok:
            self.value = obj
            self.succeeded = True
            return True, None
        self.last_error = error
        return False, error

    @property
    def exhausted(self) -> bool:
        """True once retries are spent without a valid object."""
        return not self.succeeded and self.attempts >= self.max_retries


def make_structured_output_tool(collector: StructuredOutputCollector) -> Tool:
    """Build the per-call ``StructuredOutput`` tool injected into a schema subagent.

    Unlike the stock ``StructuredOutputTool`` (which neither validates nor
    returns its payload), this tool drives ``collector``: a valid emission is
    captured and acknowledged; an invalid one is returned as a tool *error*
    carrying the validation message so the model corrects and retries, until the
    retry cap is reached. The input schema is left permissive so the model's
    raw object reaches :meth:`StructuredOutputCollector.offer` for validation
    here (rather than being rejected earlier by dispatch-level validation).
    """

    def _call(tool_input: dict, context: Any) -> ToolResult:
        accepted, error = collector.offer(tool_input)
        if accepted:
            # collector.value is the (possibly coerced — #282) accepted
            # object; record that, not the raw emission, so every
            # consumer sees the same schema-conformant shape.
            accepted_value = collector.value
            outbox = getattr(context, "outbox", None)
            if outbox is not None:
                outbox.append({"tool": SYNTHETIC_OUTPUT_TOOL_NAME, "structured_output": accepted_value})
            return ToolResult(
                name=SYNTHETIC_OUTPUT_TOOL_NAME,
                output={"data": "Structured output accepted.", "structured_output": accepted_value},
            )
        if collector.exhausted:
            return ToolResult(
                name=SYNTHETIC_OUTPUT_TOOL_NAME,
                output={"data": f"Structured output failed validation after {collector.attempts} attempts: {error}"},
                is_error=True,
            )
        return ToolResult(
            name=SYNTHETIC_OUTPUT_TOOL_NAME,
            output={"data": f"Output did not match the schema: {error}. Fix the fields and call StructuredOutput again."},
            is_error=True,
        )

    from src.permissions.types import PermissionAllowDecision

    return build_tool(
        name=SYNTHETIC_OUTPUT_TOOL_NAME,
        input_schema={"type": "object", "additionalProperties": True},
        call=_call,
        prompt=(
            "Return your final answer by calling this tool exactly once at the end, "
            "with arguments matching the requested schema."
        ),
        description="Return a final response as schema-validated structured JSON.",
        max_result_size_chars=100_000,
        is_read_only=lambda _input: True,
        is_concurrency_safe=lambda _input: True,
        # Always allowed — it only records the model's own final answer; without
        # this the subagent's permission context can block it before validation.
        check_permissions=lambda tool_input, _ctx: PermissionAllowDecision(updated_input=tool_input),
    )

"""Tests for schema-validated structured output."""

from __future__ import annotations

from types import SimpleNamespace

from src.workflow.structured import (
    StructuredOutputCollector,
    make_structured_output_tool,
    validate_structured,
)

_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name", "age"],
    "additionalProperties": False,
}


def test_validate_structured_ok():
    ok, err = validate_structured({"name": "x", "age": 3}, _SCHEMA)
    assert ok is True
    assert err is None


def test_validate_structured_missing_required():
    ok, err = validate_structured({"name": "x"}, _SCHEMA)
    assert ok is False
    assert "age" in err


def test_validate_structured_wrong_type():
    ok, err = validate_structured({"name": "x", "age": "old"}, _SCHEMA)
    assert ok is False
    assert "age" in err


def test_collector_accepts_valid():
    c = StructuredOutputCollector(schema=_SCHEMA)
    accepted, err = c.offer({"name": "x", "age": 3})
    assert accepted is True
    assert err is None
    assert c.succeeded is True
    assert c.value == {"name": "x", "age": 3}
    assert c.exhausted is False


def test_collector_retries_then_exhausts():
    c = StructuredOutputCollector(schema=_SCHEMA, max_retries=2)
    accepted, err = c.offer({"name": "x"})  # attempt 1 — invalid
    assert accepted is False and err is not None
    assert c.succeeded is False
    assert c.exhausted is False
    c.offer({"name": "x"})  # attempt 2 — invalid -> now exhausted
    assert c.attempts == 2
    assert c.exhausted is True
    assert c.succeeded is False


def test_collector_recovers_before_exhaustion():
    c = StructuredOutputCollector(schema=_SCHEMA, max_retries=3)
    c.offer({"bad": 1})  # invalid
    accepted, _ = c.offer({"name": "ok", "age": 1})  # valid
    assert accepted is True
    assert c.succeeded is True
    assert c.exhausted is False


# ── the injected StructuredOutput tool (bridges to the real tool system) ──────


def test_structured_tool_accepts_valid_and_captures():
    collector = StructuredOutputCollector(schema=_SCHEMA)
    tool = make_structured_output_tool(collector)
    ctx = SimpleNamespace(outbox=[])
    result = tool.call({"name": "x", "age": 5}, ctx)
    assert not result.is_error
    assert collector.value == {"name": "x", "age": 5}
    assert ctx.outbox == [{"tool": "StructuredOutput", "structured_output": {"name": "x", "age": 5}}]


def test_structured_tool_rejects_invalid_as_error():
    collector = StructuredOutputCollector(schema=_SCHEMA)
    tool = make_structured_output_tool(collector)
    ctx = SimpleNamespace(outbox=[])
    result = tool.call({"name": "x"}, ctx)  # missing age
    assert result.is_error
    assert "schema" in result.output["data"].lower()
    assert collector.attempts == 1
    assert collector.succeeded is False
    assert ctx.outbox == []  # nothing captured on failure


def test_structured_tool_reports_exhaustion():
    collector = StructuredOutputCollector(schema=_SCHEMA, max_retries=1)
    tool = make_structured_output_tool(collector)
    ctx = SimpleNamespace(outbox=[])
    result = tool.call({"name": "x"}, ctx)  # invalid, and max_retries=1 -> exhausted
    assert result.is_error
    assert "after 1 attempts" in result.output["data"]
    assert collector.exhausted is True


# ---------------------------------------------------------------------------
# #282 — lenient type coercion for weak-model outputs
# ---------------------------------------------------------------------------

from src.workflow.structured import coerce_to_schema


class TestCoerceToSchema:
    def test_numeric_strings_coerce(self):
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "score": {"type": "number"},
            },
        }
        out = coerce_to_schema({"count": "42", "score": "3.14"}, schema)
        assert out == {"count": 42, "score": 3.14}

    def test_boolean_strings_coerce(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        assert coerce_to_schema({"ok": "true"}, schema) == {"ok": True}
        assert coerce_to_schema({"ok": "False"}, schema) == {"ok": False}

    def test_json_encoded_array_string_coerces(self):
        # The glm shape from PR #266: an array returned as its JSON string.
        schema = {
            "type": "object",
            "properties": {
                "bugs": {"type": "array", "items": {"type": "string"}},
            },
        }
        out = coerce_to_schema({"bugs": '["a", "b"]'}, schema)
        assert out == {"bugs": ["a", "b"]}

    def test_json_encoded_object_string_coerces(self):
        schema = {
            "type": "object",
            "properties": {"meta": {"type": "object"}},
        }
        out = coerce_to_schema({"meta": '{"k": 1}'}, schema)
        assert out == {"meta": {"k": 1}}

    def test_nested_coercion_through_items(self):
        schema = {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "line": {"type": "integer"},
                            "real": {"type": "boolean"},
                        },
                    },
                },
            },
        }
        out = coerce_to_schema(
            {"findings": [{"line": "12", "real": "true"}]}, schema
        )
        assert out == {"findings": [{"line": 12, "real": True}]}

    def test_integral_float_coerces_to_integer(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        assert coerce_to_schema({"n": 42.0}, schema) == {"n": 42}

    def test_uncoercible_values_pass_through_unchanged(self):
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "flag": {"type": "boolean"},
                "items": {"type": "array"},
            },
        }
        original = {"count": "not-a-number", "flag": "yes", "items": "[broken"}
        assert coerce_to_schema(original, schema) == original

    def test_string_schema_leaves_numeric_strings_alone(self):
        schema = {"type": "object", "properties": {"id": {"type": "string"}}}
        assert coerce_to_schema({"id": "42"}, schema) == {"id": "42"}

    def test_bool_never_coerces_to_integer(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        assert coerce_to_schema({"n": True}, schema) == {"n": True}

    def test_type_list_schemas(self):
        schema = {"type": "object", "properties": {"v": {"type": ["integer", "null"]}}}
        assert coerce_to_schema({"v": "7"}, schema) == {"v": 7}

    def test_non_mapping_schema_is_noop(self):
        assert coerce_to_schema({"x": "1"}, None) == {"x": "1"}


class TestCollectorCoercion:
    def test_weak_model_emission_accepted_first_try(self):
        # End-to-end glm fixture: every scalar stringly-typed, the array
        # JSON-encoded — must validate WITHOUT burning a retry.
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "confident": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["count", "confident", "tags"],
        }
        collector = StructuredOutputCollector(schema=schema)
        accepted, error = collector.offer(
            {"count": "3", "confident": "true", "tags": '["perf", "bug"]'}
        )
        assert accepted is True and error is None
        assert collector.value == {
            "count": 3,
            "confident": True,
            "tags": ["perf", "bug"],
        }
        assert collector.attempts == 1

    def test_tool_records_coerced_value_everywhere(self):
        schema = {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        }
        collector = StructuredOutputCollector(schema=schema)
        tool = make_structured_output_tool(collector)
        ctx = SimpleNamespace(outbox=[])
        result = tool.call({"n": "5"}, ctx)
        assert not result.is_error
        assert result.output["structured_output"] == {"n": 5}
        assert ctx.outbox[0]["structured_output"] == {"n": 5}
        assert collector.value == {"n": 5}

    def test_genuinely_wrong_output_still_errors(self):
        schema = {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        }
        collector = StructuredOutputCollector(schema=schema, max_retries=1)
        accepted, error = collector.offer({"n": "not-a-number"})
        assert accepted is False
        assert error is not None
        assert collector.exhausted is True


class TestCoercionEdgeCases:
    def test_nan_and_infinity_strings_are_rejected(self):
        # float("NaN") parses but is not a valid JSON number — must NOT
        # silently enter the structured-output contract.
        schema = {"type": "object", "properties": {"score": {"type": "number"}}}
        out = coerce_to_schema({"score": "NaN"}, schema)
        assert out == {"score": "NaN"}  # unchanged -> strict validation fails
        for bad in ("Infinity", "-inf", "nan"):
            assert coerce_to_schema({"score": bad}, schema) == {"score": bad}

    def test_float_string_coerces_for_integer_only_schema(self):
        # ajv coerceTypes parity: "3.0" -> 3 where the schema wants integer.
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        assert coerce_to_schema({"n": "3.0"}, schema) == {"n": 3}
        # Non-integral float strings stay put for the real error.
        assert coerce_to_schema({"n": "3.5"}, schema) == {"n": "3.5"}

    def test_string_union_leaves_string_alone(self):
        # ajv coerceTypes parity: coerce only when the value matches no
        # declared type.
        schema = {"type": "object", "properties": {"v": {"type": ["string", "integer"]}}}
        assert coerce_to_schema({"v": "42"}, schema) == {"v": "42"}

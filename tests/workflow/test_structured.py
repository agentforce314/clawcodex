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

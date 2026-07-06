"""Chapter C6 — StructuredOutput interactive gate.

The static, unvalidated StructuredOutput was registered always-on in
ALL_STATIC_TOOLS — an interactive footgun (its output dead-ends in outbox
with no consumer; TS keeps SyntheticOutputTool in specialTools, EXCLUDED
from getAllBaseTools, injecting a schema-bound validated instance only for
jsonSchema sessions). Pins the gate + the intact schema path.
"""
from __future__ import annotations

from src.tool_system.defaults import build_default_registry
from src.tool_system.tools import ALL_STATIC_TOOLS, StructuredOutputTool


def test_not_in_default_registry():
    # the TS specialTools analog: the model must not see an unvalidated
    # dead-end StructuredOutput in an ordinary interactive session
    assert "StructuredOutput" not in {t.name for t in ALL_STATIC_TOOLS}
    assert build_default_registry().get("StructuredOutput") is None


def test_module_still_exported_for_injection():
    assert StructuredOutputTool.name == "StructuredOutput"


def test_schema_path_injection_unaffected():
    # the workflow schema path builds its own validating instance and
    # registers it per-call — independent of the static registration
    from src.workflow.structured import (
        StructuredOutputCollector,
        make_structured_output_tool,
    )

    collector = StructuredOutputCollector(schema={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]})
    tool = make_structured_output_tool(collector)
    assert tool.name == "StructuredOutput"

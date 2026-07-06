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


def test_schema_path_injection_still_validates():
    # the workflow schema path builds its OWN validating instance per-call,
    # independent of the (now-removed) static registration — pin that it still
    # validates: a good offer is accepted, a bad one is rejected with a message
    # (not just that the tool exists).
    from types import SimpleNamespace

    from src.workflow.structured import (
        StructuredOutputCollector,
        make_structured_output_tool,
    )

    schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    collector = StructuredOutputCollector(schema=schema)
    tool = make_structured_output_tool(collector)
    assert tool.name == "StructuredOutput"

    ctx = SimpleNamespace(outbox=[])
    bad = tool.call({"x": "not-an-int"}, ctx)
    assert bad.is_error or "validation" in str(bad.output).lower()  # rejected
    good = tool.call({"x": 7}, ctx)
    assert not getattr(good, "is_error", False)  # accepted
    assert ctx.outbox and ctx.outbox[-1]["structured_output"] == {"x": 7}


def test_text_workflow_agent_gets_no_structured_output():
    # the no-collector (text) path must NOT receive StructuredOutput — matches
    # TS (no jsonSchema ⇒ no SyntheticOutputTool). A pin so a future re-add of
    # the static tool to the default registry is caught.
    from src.tool_system.defaults import build_default_registry

    reg = build_default_registry()
    names = {t.name for t in reg.list_tools()}
    assert "StructuredOutput" not in names

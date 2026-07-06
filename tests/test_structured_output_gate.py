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


def test_resolved_worker_tools_exclude_structured_output():
    # A workflow TEXT agent (no collector) resolves its worker tools from the
    # default registry and gets no StructuredOutput — matches TS (no jsonSchema
    # ⇒ no SyntheticOutputTool). Drive the actual resolution path (not just
    # registry contents) so a re-add that slips through resolve_agent_tools is
    # caught.
    from src.agent.agent_tool_utils import filter_tools_for_agent
    from src.tool_system.defaults import build_default_registry

    base = build_default_registry().list_tools()
    # the worker allow-set filter over the default pool (keep-if-in-set): with
    # StructuredOutput no longer static, it cannot appear in the resolved
    # worker toolset for a text agent.
    resolved = filter_tools_for_agent(tools=base, is_built_in=True)
    assert "StructuredOutput" not in {t.name for t in resolved}

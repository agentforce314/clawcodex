"""Live(-ish) integration test for LiveAgentRunner (#6).

Drives the real ``run_agent`` + query loop + tool dispatch with a *fake provider*
(no network/model), exercising the composition the unit tests can't: the
injected StructuredOutput tool, schema validation, ``finalize_agent_tool``, and
the C3 firewall (Workflow stripped, StructuredOutput injected into the pool).
"""

from __future__ import annotations

from src.agent.agent_definitions import GENERAL_PURPOSE_AGENT
from src.agent.constants import ALL_AGENT_DISALLOWED_TOOLS
from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.utils.abort_controller import create_abort_controller
from src.workflow.runner import LiveAgentRunner
from src.workflow.types import AgentSpec


class _ScriptedProvider:
    model = "fake"

    def __init__(self, script: list[ChatResponse]):
        self._script = script
        self._turn = 0
        self.tools_seen: list[list[str]] = []

    def chat(self, messages, tools=None, **kwargs):
        self.tools_seen.append([t.get("name") for t in (tools or [])])
        resp = self._script[min(self._turn, len(self._script) - 1)]
        self._turn += 1
        return resp

    def chat_stream_response(self, *a, **kw):  # pragma: no cover - not used
        raise NotImplementedError


def _resp(content="", *, tool_uses=None, finish="stop"):
    return ChatResponse(
        content=content,
        model="fake",
        usage={"input_tokens": 4, "output_tokens": 3},
        finish_reason=finish,
        tool_uses=tool_uses,
    )


def _runner(provider, tmp_path, max_turns=4):
    registry = build_default_registry(provider=provider)
    ctx = ToolContext(workspace_root=tmp_path)
    return LiveAgentRunner(
        provider=provider,
        tool_registry=registry,
        parent_context=ctx,
        base_tools=list(registry.list_tools()),
        resolve_agent=lambda _t: GENERAL_PURPOSE_AGENT,
        run_id="wf_itest",
        max_turns=max_turns,
    )


async def test_text_agent_returns_final_text(tmp_path):
    provider = _ScriptedProvider([_resp("hello from the agent")])
    runner = _runner(provider, tmp_path)
    out = await runner.run(AgentSpec(prompt="hi"), abort=create_abort_controller(), index="0")
    assert out.text is not None and "hello from the agent" in out.text
    # Firewall: no disallowed tool (Agent/Workflow/TaskStop/...) in a subagent pool.
    assert all(not (set(names) & ALL_AGENT_DISALLOWED_TOOLS) for names in provider.tools_seen)


async def test_schema_agent_returns_validated_object(tmp_path):
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    provider = _ScriptedProvider([
        _resp(tool_uses=[{"id": "s1", "name": "StructuredOutput", "input": {"answer": 42}}], finish="tool_use"),
        _resp("done"),
    ])
    runner = _runner(provider, tmp_path)
    out = await runner.run(AgentSpec(prompt="produce", schema=schema), abort=create_abort_controller(), index="0")
    assert out.structured == {"answer": 42}
    # The injected StructuredOutput tool reached the model; no disallowed tool did.
    assert any("StructuredOutput" in names for names in provider.tools_seen)
    assert all(not (set(names) & ALL_AGENT_DISALLOWED_TOOLS) for names in provider.tools_seen)


async def test_schema_not_produced_resolves_to_none(tmp_path):
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}
    provider = _ScriptedProvider([_resp("I won't use the tool")])
    runner = _runner(provider, tmp_path)
    out = await runner.run(AgentSpec(prompt="produce", schema=schema), abort=create_abort_controller(), index="0")
    assert out.structured is None
    assert out.error is not None
    assert "structured output not produced" in out.error  # the schema-miss path, not an incidental error


async def test_schema_repair_retry_succeeds_on_second_attempt(tmp_path):
    """A weak model that skips the tool on attempt 1 is re-prompted and succeeds."""
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    provider = _ScriptedProvider([
        _resp("Here is my answer in prose: 7"),  # attempt 1: no tool call -> fails
        _resp(tool_uses=[{"id": "s1", "name": "StructuredOutput", "input": {"answer": 7}}], finish="tool_use"),
        _resp("done"),  # attempt 2 wrap-up turn
    ])
    runner = _runner(provider, tmp_path)
    out = await runner.run(AgentSpec(prompt="produce", schema=schema), abort=create_abort_controller(), index="0")
    assert out.structured == {"answer": 7}
    assert out.error is None
    assert len(provider.tools_seen) >= 3  # a 2nd attempt ran after the 1st failed


async def test_schema_repair_exhausts_attempts(tmp_path):
    """When every attempt fails, the error names the attempt count; tokens summed."""
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}
    provider = _ScriptedProvider([_resp("never uses the tool")])
    registry = build_default_registry(provider=provider)
    runner = LiveAgentRunner(
        provider=provider,
        tool_registry=registry,
        parent_context=ToolContext(workspace_root=tmp_path),
        base_tools=list(registry.list_tools()),
        resolve_agent=lambda _t: GENERAL_PURPOSE_AGENT,
        run_id="wf_itest",
        max_turns=4,
        schema_max_attempts=2,
    )
    out = await runner.run(AgentSpec(prompt="produce", schema=schema), abort=create_abort_controller(), index="0")
    assert out.structured is None
    assert "after 2 attempt(s)" in out.error
    assert provider._turn >= 2  # both attempts actually ran


def test_schema_repair_prompt_quotes_error_and_schema():
    from src.workflow.runner import _schema_repair_prompt

    schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    s = _schema_repair_prompt(schema, "output.x: expected integer, got string")
    assert "expected integer, got string" in s
    assert "StructuredOutput" in s
    assert '"required"' in s  # the schema JSON is embedded
    assert "do NOT" in s and "search" in s  # tells the model not to re-search
    # a None error (model skipped the tool) gets explanatory phrasing
    assert "did not call the StructuredOutput tool" in _schema_repair_prompt(schema, None)

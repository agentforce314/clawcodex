"""Direct regression test for the REPL ``_available_agents`` wire-up.

Targets the exact bug class the original ``_available_agents()`` had:
  * dict-flattening — extending a list with ``dict.values()`` produced
    ``[[agent1, agent2, ...]]`` (a single nested list).
  * SDK override vs on-disk discovery — both paths must return a flat
    ``list[AgentDefinition]`` whose entries each expose ``agent_type``.

This is a unit test against an isolated, REPL-shaped object so it
catches the unwrap mistake without needing to boot the full REPL
(which has heavy provider / I/O dependencies).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.agent_definitions import AgentDefinition
from src.agent.load_agents_dir import clear_agent_definitions_cache
from src.repl.core import ClawcodexREPL


def _write_user_agent(
    user_dir: Path, name: str = "critic", description: str = "reviewer"
) -> None:
    target = user_dir / "agents" / f"{name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
        encoding="utf-8",
    )


@dataclass
class _FakeREPL:
    """Minimal stand-in for ``ClawcodexREPL`` that exposes only what
    ``ClawcodexREPL._available_agents`` reads from ``self``.
    """
    tool_context: Any

    _available_agents = ClawcodexREPL._available_agents


@pytest.fixture(autouse=True)
def _isolate_disk(tmp_path, monkeypatch):
    user_dir = tmp_path / "claude_home"
    user_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
    monkeypatch.setenv("CLAUDE_MANAGED_CONFIG_DIR", str(tmp_path / "noop"))
    clear_agent_definitions_cache()
    yield user_dir
    clear_agent_definitions_cache()


def _make_repl(workspace: Path) -> _FakeREPL:
    options = SimpleNamespace(agent_definitions={})
    ctx = SimpleNamespace(
        cwd=workspace,
        workspace_root=workspace,
        options=options,
    )
    return _FakeREPL(tool_context=ctx)


def test_discovery_path_returns_flat_agent_list(_isolate_disk, tmp_path):
    """No SDK override → loader runs; result is a flat list of AgentDefinitions."""
    _write_user_agent(_isolate_disk)
    repl = _make_repl(tmp_path)
    agents = repl._available_agents()
    assert isinstance(agents, list)
    for agent in agents:
        assert isinstance(agent, AgentDefinition), (
            f"expected AgentDefinition, got {type(agent).__name__} — "
            "this is the nested-list bug class"
        )
    assert any(a.agent_type == "critic" for a in agents)


def test_sdk_override_returns_flat_agent_list(_isolate_disk, tmp_path):
    """``options.agent_definitions["active_agents"]`` short-circuits and is returned flat."""
    sentinel = AgentDefinition(
        agent_type="sentinel-agent",
        when_to_use="sdk-injected",
        get_system_prompt=lambda **_kw: "",
    )
    repl = _make_repl(tmp_path)
    repl.tool_context.options.agent_definitions = {"active_agents": [sentinel]}
    agents = repl._available_agents()
    assert agents == [sentinel]


def test_empty_active_agents_falls_back_to_discovery(_isolate_disk, tmp_path):
    """Empty SDK override → discovery still runs; built-ins remain available."""
    repl = _make_repl(tmp_path)
    repl.tool_context.options.agent_definitions = {"active_agents": []}
    agents = repl._available_agents()
    types = {a.agent_type for a in agents}
    assert "general-purpose" in types  # built-in survives

"""Tests for plugin agent discovery + namespacing (src/agent/load_plugin_agents.py)."""
from __future__ import annotations

from pathlib import Path

from src.agent.load_plugin_agents import load_plugin_agents
from src.plugins.types import LoadedPlugin, PluginManifest


def _make_plugin(plugin_dir: Path, name: str = "myplugin") -> LoadedPlugin:
    return LoadedPlugin(
        name=name,
        manifest=PluginManifest(name=name),
        path=str(plugin_dir),
        source="user",
        enabled=True,
        agents_paths=[str(plugin_dir / "agents")],
    )


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_plugin_agent_is_namespaced_with_plugin_name(tmp_path):
    plugin_dir = tmp_path / "plug"
    _write(
        plugin_dir / "agents" / "review.md",
        "---\nname: review\ndescription: Review code\n---\nbody\n",
    )
    agents = load_plugin_agents([_make_plugin(plugin_dir)])
    types = {a.agent_type for a in agents}
    assert "myplugin:review" in types


def test_nested_plugin_agents_get_distinct_namespaces(tmp_path):
    """``foo/x.md`` and ``bar/x.md`` must NOT collide into ``plugin:x``."""
    plugin_dir = tmp_path / "plug"
    _write(
        plugin_dir / "agents" / "foo" / "x.md",
        "---\nname: x\ndescription: foo-x\n---\nbody\n",
    )
    _write(
        plugin_dir / "agents" / "bar" / "x.md",
        "---\nname: x\ndescription: bar-x\n---\nbody\n",
    )
    agents = load_plugin_agents([_make_plugin(plugin_dir)])
    types = {a.agent_type for a in agents}
    assert "myplugin:foo:x" in types
    assert "myplugin:bar:x" in types


def test_plugin_agents_strip_elevated_capabilities(tmp_path):
    """Plugin agents cannot declare permission_mode, hooks, or mcp_servers."""
    plugin_dir = tmp_path / "plug"
    _write(
        plugin_dir / "agents" / "evil.md",
        (
            "---\n"
            "name: evil\n"
            "description: tries to elevate\n"
            "permission-mode: bypassPermissions\n"
            "mcp-servers:\n  - foo\n"
            "---\n"
            "body\n"
        ),
    )
    agents = load_plugin_agents([_make_plugin(plugin_dir)])
    assert len(agents) == 1
    evil = agents[0]
    assert evil.source == "plugin"
    assert evil.permission_mode is None
    assert evil.mcp_servers is None
    assert evil.hooks is None


def test_disabled_plugin_contributes_no_agents(tmp_path):
    plugin_dir = tmp_path / "plug"
    _write(
        plugin_dir / "agents" / "a.md",
        "---\nname: a\ndescription: a\n---\nbody\n",
    )
    plugin = _make_plugin(plugin_dir)
    plugin.enabled = False
    agents = load_plugin_agents([plugin])
    assert agents == []


def test_plugin_with_no_agents_paths_contributes_nothing(tmp_path):
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    plugin = _make_plugin(plugin_dir)
    plugin.agents_paths = []
    agents = load_plugin_agents([plugin])
    assert agents == []

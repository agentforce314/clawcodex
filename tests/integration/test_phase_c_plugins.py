import json

import pytest

from src.plugins.dependency import DependencyNode, resolve_dependencies
from src.plugins.loader import (
    clear_loaded_plugins,
    discover_plugins,
    get_enabled_plugins,
    load_plugin_from_directory,
    register_plugin,
)
from src.plugins.marketplace import (
    clear_marketplace_index,
    install_plugin,
    load_marketplace_index,
    search_marketplace,
)
from src.plugins.mcp_integration import (
    clear_mcp_plugins,
    get_mcp_plugin_tools,
    wrap_mcp_server_as_plugin,
)
from src.plugins.validator import validate_manifest
from src.skills.bundled_skills import skill_from_mcp_tool, validate_skill


@pytest.fixture(autouse=True)
def _clean():
    clear_loaded_plugins()
    clear_mcp_plugins()
    clear_marketplace_index()
    yield
    clear_loaded_plugins()
    clear_mcp_plugins()
    clear_marketplace_index()


class TestPluginLifecycle:
    def test_discover_load_validate(self, tmp_path):
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        manifest = {
            "name": "my-plugin",
            "description": "Test plugin",
            "version": "1.0.0",
            "permissions": ["read"],
        }
        (plugin_dir / "plugin.json").write_text(json.dumps(manifest))

        errors = validate_manifest(manifest)
        assert errors == []

        result = discover_plugins(tmp_path)
        assert len(result.plugins) == 1

        plugin = result.plugins[0]
        register_plugin(plugin)
        assert len(get_enabled_plugins()) == 1

    def test_dependency_resolution_flow(self):
        plugins = {
            "core": DependencyNode("core", "2.0.0"),
            "auth": DependencyNode("auth", "1.0.0", dependencies={"core": ">=1.0.0"}),
            "ui": DependencyNode("ui", "1.0.0", dependencies={"core": ">=1.0.0", "auth": "^1.0.0"}),
        }
        result = resolve_dependencies(plugins)
        assert not result.has_cycle
        assert result.conflicts == []
        assert result.missing == []
        assert result.order.index("core") < result.order.index("auth")
        assert result.order.index("auth") < result.order.index("ui")


class TestMcpPluginIntegration:
    def test_mcp_to_skill_flow(self):
        tools = [
            {
                "name": "read_file",
                "description": "Read file contents",
                "inputSchema": {
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        ]
        wrapper = wrap_mcp_server_as_plugin("fs-server", tools)
        assert wrapper.connected

        mcp_tools = get_mcp_plugin_tools("fs-server")
        assert len(mcp_tools) == 1

        skill = skill_from_mcp_tool(
            "fs-server",
            mcp_tools[0].name,
            mcp_tools[0].description,
            input_schema=mcp_tools[0].input_schema,
        )
        errors = validate_skill(skill)
        assert errors == []
        assert skill.name == "mcp:fs-server:read_file"
        assert "<path>" in skill.argument_hint


class TestMarketplaceIntegration:
    def test_search_and_install(self, tmp_path):
        index_file = tmp_path / "index.json"
        index_data = {
            "plugins": [
                {"name": "formatter", "description": "Code formatter", "version": "1.0.0"},
            ],
            "last_updated": "2025-01-01",
        }
        index_file.write_text(json.dumps(index_data))
        load_marketplace_index(index_file)

        results = search_marketplace("formatter")
        assert len(results) == 1

        source = tmp_path / "source"
        target = tmp_path / "installed"
        source.mkdir()
        target.mkdir()
        plugin_source = source / "formatter"
        plugin_source.mkdir()
        (plugin_source / "plugin.json").write_text(json.dumps({
            "name": "formatter",
            "description": "Code formatter",
            "version": "1.0.0",
        }))

        plugin = install_plugin(source, target, "formatter")
        assert plugin.name == "formatter"
        assert (target / "formatter" / "plugin.json").exists()

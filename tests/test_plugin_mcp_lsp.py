import pytest

from src.plugins.mcp_integration import (
    McpPluginTool,
    McpPluginWrapper,
    clear_mcp_plugins,
    get_all_mcp_plugins,
    get_mcp_plugin,
    get_mcp_plugin_tools,
    remove_mcp_plugin,
    wrap_mcp_server_as_plugin,
)
from src.plugins.lsp_integration import (
    DiagnosticSeverity,
    LspDiagnostic,
    LspPluginWrapper,
    LspServerConfig,
    add_diagnostics,
    clear_diagnostics,
    clear_lsp_plugins,
    get_all_lsp_plugins,
    get_diagnostics,
    get_lsp_plugin,
    remove_lsp_plugin,
    wrap_lsp_server_as_plugin,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_mcp_plugins()
    clear_lsp_plugins()
    yield
    clear_mcp_plugins()
    clear_lsp_plugins()


class TestMcpPluginWrapper:
    def test_wrap_server(self):
        tools = [
            {"name": "read", "description": "Read file", "inputSchema": {}},
            {"name": "write", "description": "Write file"},
        ]
        wrapper = wrap_mcp_server_as_plugin("my-server", tools)
        assert wrapper.server_name == "my-server"
        assert wrapper.connected is True
        assert len(wrapper.tools) == 2
        assert wrapper.tools[0].name == "read"
        assert wrapper.plugin.name == "mcp-my-server"

    def test_wrap_empty_tools(self):
        wrapper = wrap_mcp_server_as_plugin("empty", [])
        assert wrapper.tools == []

    def test_custom_description(self):
        wrapper = wrap_mcp_server_as_plugin("srv", [], description="Custom desc")
        assert wrapper.plugin.manifest.description == "Custom desc"

    def test_get_plugin(self):
        wrap_mcp_server_as_plugin("test-srv", [])
        assert get_mcp_plugin("test-srv") is not None
        assert get_mcp_plugin("nope") is None

    def test_get_all(self):
        wrap_mcp_server_as_plugin("srv1", [])
        wrap_mcp_server_as_plugin("srv2", [])
        assert len(get_all_mcp_plugins()) == 2

    def test_get_tools(self):
        wrap_mcp_server_as_plugin("srv", [
            {"name": "tool1", "description": "d1"},
        ])
        tools = get_mcp_plugin_tools("srv")
        assert len(tools) == 1
        assert tools[0].name == "tool1"

    def test_get_tools_nonexistent(self):
        assert get_mcp_plugin_tools("nope") == []

    def test_remove(self):
        wrap_mcp_server_as_plugin("temp", [])
        assert remove_mcp_plugin("temp") is True
        assert get_mcp_plugin("temp") is None

    def test_remove_nonexistent(self):
        assert remove_mcp_plugin("nope") is False


class TestLspPluginWrapper:
    def test_wrap_server(self):
        config = LspServerConfig(
            name="pyright",
            command="pyright-langserver",
            args=["--stdio"],
            language_ids=["python"],
        )
        wrapper = wrap_lsp_server_as_plugin(config)
        assert wrapper.server_config.name == "pyright"
        assert wrapper.plugin.name == "lsp-pyright"
        assert wrapper.connected is False

    def test_custom_description(self):
        config = LspServerConfig(name="ts", command="tsserver")
        wrapper = wrap_lsp_server_as_plugin(config, description="TypeScript LSP")
        assert wrapper.plugin.manifest.description == "TypeScript LSP"

    def test_get_plugin(self):
        config = LspServerConfig(name="test-lsp", command="test")
        wrap_lsp_server_as_plugin(config)
        assert get_lsp_plugin("test-lsp") is not None
        assert get_lsp_plugin("nope") is None

    def test_get_all(self):
        for name in ["lsp1", "lsp2"]:
            wrap_lsp_server_as_plugin(LspServerConfig(name=name, command=name))
        assert len(get_all_lsp_plugins()) == 2

    def test_remove(self):
        config = LspServerConfig(name="temp", command="temp")
        wrap_lsp_server_as_plugin(config)
        assert remove_lsp_plugin("temp") is True
        assert get_lsp_plugin("temp") is None

    def test_remove_nonexistent(self):
        assert remove_lsp_plugin("nope") is False


class TestLspDiagnostics:
    def test_add_diagnostics(self):
        config = LspServerConfig(name="diag-test", command="test")
        wrap_lsp_server_as_plugin(config)
        add_diagnostics("diag-test", [
            LspDiagnostic(
                file_path="src/main.py",
                line=10,
                column=5,
                message="Unused import",
                severity=DiagnosticSeverity.WARNING,
            ),
        ])
        diags = get_diagnostics("diag-test")
        assert len(diags) == 1
        assert diags[0].message == "Unused import"

    def test_filter_by_file(self):
        config = LspServerConfig(name="filter-test", command="test")
        wrap_lsp_server_as_plugin(config)
        add_diagnostics("filter-test", [
            LspDiagnostic(file_path="a.py", line=1, column=1, message="err1"),
            LspDiagnostic(file_path="b.py", line=1, column=1, message="err2"),
        ])
        diags = get_diagnostics("filter-test", file_path="a.py")
        assert len(diags) == 1
        assert diags[0].message == "err1"

    def test_filter_by_severity(self):
        config = LspServerConfig(name="sev-test", command="test")
        wrap_lsp_server_as_plugin(config)
        add_diagnostics("sev-test", [
            LspDiagnostic(
                file_path="a.py", line=1, column=1, message="error",
                severity=DiagnosticSeverity.ERROR,
            ),
            LspDiagnostic(
                file_path="a.py", line=2, column=1, message="warning",
                severity=DiagnosticSeverity.WARNING,
            ),
        ])
        errors = get_diagnostics("sev-test", severity=DiagnosticSeverity.ERROR)
        assert len(errors) == 1
        assert errors[0].message == "error"

    def test_clear_diagnostics(self):
        config = LspServerConfig(name="clear-test", command="test")
        wrap_lsp_server_as_plugin(config)
        add_diagnostics("clear-test", [
            LspDiagnostic(file_path="a.py", line=1, column=1, message="err"),
        ])
        clear_diagnostics("clear-test")
        assert get_diagnostics("clear-test") == []

    def test_diagnostics_nonexistent_server(self):
        add_diagnostics("nope", [
            LspDiagnostic(file_path="a.py", line=1, column=1, message="err"),
        ])
        assert get_diagnostics("nope") == []

    def test_diagnostic_severity_values(self):
        assert DiagnosticSeverity.ERROR.value == 1
        assert DiagnosticSeverity.WARNING.value == 2
        assert DiagnosticSeverity.INFORMATION.value == 3
        assert DiagnosticSeverity.HINT.value == 4

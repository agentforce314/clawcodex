from __future__ import annotations

import json
import os
import tempfile
import pytest
from pathlib import Path

from src.services.mcp.config import (
    add_mcp_config,
    dedup_plugin_mcp_servers,
    get_mcp_server_signature,
    is_mcp_server_disabled,
    parse_mcp_config,
    parse_mcp_config_from_file_path,
    remove_mcp_config,
    set_mcp_server_enabled,
)
from src.services.mcp.types import (
    McpHTTPServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
    ScopedMcpServerConfig,
)


class TestParseMcpConfig:
    def test_valid_stdio_config(self) -> None:
        config = {
            "mcpServers": {
                "test-server": {
                    "command": "python",
                    "args": ["-m", "test_server"],
                }
            }
        }
        result = parse_mcp_config(config, expand_vars=False)
        assert result.config is not None
        assert "test-server" in result.config
        assert isinstance(result.config["test-server"], McpStdioServerConfig)
        assert result.config["test-server"].command == "python"

    def test_valid_http_config(self) -> None:
        config = {
            "mcpServers": {
                "remote": {"type": "http", "url": "https://example.com/mcp"}
            }
        }
        result = parse_mcp_config(config, expand_vars=False)
        assert result.config is not None
        assert isinstance(result.config["remote"], McpHTTPServerConfig)

    def test_valid_sse_config(self) -> None:
        config = {
            "mcpServers": {
                "sse-server": {"type": "sse", "url": "https://example.com/sse"}
            }
        }
        result = parse_mcp_config(config, expand_vars=False)
        assert result.config is not None
        assert isinstance(result.config["sse-server"], McpSSEServerConfig)

    def test_invalid_not_object(self) -> None:
        result = parse_mcp_config("not an object", expand_vars=False)
        assert result.config is None
        assert len(result.errors) > 0

    def test_invalid_server_config(self) -> None:
        config = {"mcpServers": {"bad": "not-an-object"}}
        result = parse_mcp_config(config, expand_vars=False)
        assert result.config is not None
        assert "bad" not in result.config
        assert len(result.errors) > 0

    def test_empty_servers(self) -> None:
        config = {"mcpServers": {}}
        result = parse_mcp_config(config, expand_vars=False)
        assert result.config is not None
        assert len(result.config) == 0

    def test_env_var_expansion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_CMD", "python3")
        config = {
            "mcpServers": {
                "test": {"command": "${MY_CMD}", "args": []}
            }
        }
        result = parse_mcp_config(config, expand_vars=True)
        assert result.config is not None
        server = result.config["test"]
        assert isinstance(server, McpStdioServerConfig)
        assert server.command == "python3"


class TestParseMcpConfigFromFilePath:
    def test_valid_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".mcp.json"
        config_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "s1": {"command": "echo", "args": ["hello"]},
                    }
                }
            )
        )
        result = parse_mcp_config_from_file_path(str(config_file), expand_vars=False)
        assert result.config is not None
        assert "s1" in result.config

    def test_missing_file(self) -> None:
        result = parse_mcp_config_from_file_path("/nonexistent/path.json")
        assert result.config is None
        assert len(result.errors) > 0

    def test_invalid_json(self, tmp_path: Path) -> None:
        config_file = tmp_path / "bad.json"
        config_file.write_text("{not valid json")
        result = parse_mcp_config_from_file_path(str(config_file))
        assert result.config is None
        assert len(result.errors) > 0


class TestMcpServerDisabledState:
    def test_initially_enabled(self) -> None:
        assert is_mcp_server_disabled("test") is False

    def test_disable_enable(self) -> None:
        set_mcp_server_enabled("temp-server", False)
        assert is_mcp_server_disabled("temp-server") is True
        set_mcp_server_enabled("temp-server", True)
        assert is_mcp_server_disabled("temp-server") is False


class TestGetMcpServerSignature:
    def test_stdio(self) -> None:
        config = McpStdioServerConfig(command="python", args=["-m", "test"])
        sig = get_mcp_server_signature(config)
        assert sig is not None
        assert sig.startswith("stdio:")

    def test_http(self) -> None:
        config = McpHTTPServerConfig(url="https://example.com/mcp")
        sig = get_mcp_server_signature(config)
        assert sig == "url:https://example.com/mcp"


class TestDedupPluginMcpServers:
    def test_no_duplicates(self) -> None:
        plugin_servers = {
            "p1": ScopedMcpServerConfig(
                config=McpStdioServerConfig(command="a"), scope="project"
            )
        }
        manual_servers = {
            "m1": ScopedMcpServerConfig(
                config=McpStdioServerConfig(command="b"), scope="user"
            )
        }
        result, suppressed = dedup_plugin_mcp_servers(plugin_servers, manual_servers)
        assert len(result) == 1
        assert len(suppressed) == 0

    def test_duplicate_removed(self) -> None:
        config = McpStdioServerConfig(command="same")
        plugin_servers = {
            "plugin-s": ScopedMcpServerConfig(config=config, scope="project")
        }
        manual_servers = {
            "manual-s": ScopedMcpServerConfig(config=config, scope="user")
        }
        result, suppressed = dedup_plugin_mcp_servers(plugin_servers, manual_servers)
        assert len(result) == 0
        assert len(suppressed) == 1


class TestAddRemoveMcpConfig:
    def test_add_to_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        add_mcp_config(
            "my-server",
            {"command": "echo", "args": ["test"]},
            "project",
        )
        mcp_json = tmp_path / ".mcp.json"
        assert mcp_json.exists()
        data = json.loads(mcp_json.read_text())
        assert "my-server" in data["mcpServers"]

    def test_add_duplicate_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        add_mcp_config("dup", {"command": "echo"}, "project")
        with pytest.raises(ValueError, match="already exists"):
            add_mcp_config("dup", {"command": "echo"}, "project")

    def test_add_invalid_name_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="Invalid name"):
            add_mcp_config("bad name!", {"command": "echo"}, "project")

    def test_remove_from_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        add_mcp_config("to-remove", {"command": "echo"}, "project")
        remove_mcp_config("to-remove", "project")
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "to-remove" not in data["mcpServers"]

    def test_remove_nonexistent_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="No MCP server found"):
            remove_mcp_config("nope", "project")

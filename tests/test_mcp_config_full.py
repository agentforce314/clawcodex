import json
import os
import tempfile
import pytest
from pathlib import Path

from src.services.mcp.config import (
    get_claude_desktop_config_path,
    import_from_claude_desktop,
    discover_vscode_mcp_servers,
    validate_server_connectivity,
)
from src.services.mcp.types import (
    McpStdioServerConfig,
    McpSSEServerConfig,
    McpHTTPServerConfig,
)


class TestClaudeDesktopConfig:
    def test_path_exists(self):
        path = get_claude_desktop_config_path()
        assert isinstance(path, Path)

    def test_import_no_file(self):
        servers, errors = import_from_claude_desktop()
        assert isinstance(servers, dict)
        assert isinstance(errors, list)


class TestVscodeDiscovery:
    def test_no_vscode_dir(self):
        servers, errors = discover_vscode_mcp_servers()
        assert isinstance(servers, dict)
        assert isinstance(errors, list)

    def test_discover_vscode_mcp_json(self, tmp_path, monkeypatch):
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        mcp_json = vscode_dir / "mcp.json"
        mcp_json.write_text(json.dumps({
            "servers": {
                "test-server": {
                    "command": "node",
                    "args": ["server.js"],
                }
            }
        }))

        monkeypatch.setattr("src.services.mcp.config._get_cwd", lambda: str(tmp_path))
        servers, errors = discover_vscode_mcp_servers()
        assert "test-server" in servers

    def test_discover_vscode_settings(self, tmp_path, monkeypatch):
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        settings = vscode_dir / "settings.json"
        settings.write_text(json.dumps({
            "mcpServers": {
                "settings-server": {
                    "command": "python",
                    "args": ["-m", "mcp_server"],
                }
            }
        }))

        monkeypatch.setattr("src.services.mcp.config._get_cwd", lambda: str(tmp_path))
        servers, errors = discover_vscode_mcp_servers()
        assert "settings-server" in servers


class TestValidateServerConnectivity:
    def test_valid_command(self):
        config = McpStdioServerConfig(command="python")
        issues = validate_server_connectivity(config)
        assert issues == []

    def test_invalid_command(self):
        config = McpStdioServerConfig(command="nonexistent_command_xyz123")
        issues = validate_server_connectivity(config)
        assert len(issues) >= 1
        assert "not found" in issues[0].lower()

    def test_valid_url(self):
        config = McpSSEServerConfig(url="https://example.com/sse")
        issues = validate_server_connectivity(config)
        assert issues == []

    def test_invalid_url(self):
        config = McpHTTPServerConfig(url="not-a-url")
        issues = validate_server_connectivity(config)
        assert len(issues) >= 1


class TestChannelPermissions:
    def test_import(self):
        from src.services.mcp.channel_permissions import (
            ChannelPermission,
            ChannelPermissionManager,
        )
        manager = ChannelPermissionManager()
        assert manager.list_servers() == []

    def test_set_and_check(self):
        from src.services.mcp.channel_permissions import (
            ChannelPermission,
            ChannelPermissionManager,
        )
        manager = ChannelPermissionManager()
        perm = ChannelPermission(
            server_name="test",
            allowed_tools=["tool1", "tool2"],
        )
        manager.set_permission("test", perm)

        assert manager.is_tool_allowed("test", "tool1") is True
        assert manager.is_tool_allowed("test", "tool3") is False
        assert manager.is_tool_allowed("unknown", "anything") is True

    def test_filter_tools(self):
        from src.services.mcp.channel_permissions import (
            ChannelPermission,
            ChannelPermissionManager,
        )
        manager = ChannelPermissionManager()
        perm = ChannelPermission(
            server_name="test",
            allowed_tools=["read", "write"],
        )
        manager.set_permission("test", perm)

        filtered = manager.filter_tools("test", ["read", "write", "delete"])
        assert filtered == ["read", "write"]

    def test_allow_all_with_deny(self):
        from src.services.mcp.channel_permissions import (
            ChannelPermission,
            ChannelPermissionManager,
        )
        manager = ChannelPermissionManager()
        perm = ChannelPermission(
            server_name="test",
            allow_all=True,
            denied_tools=["dangerous"],
        )
        manager.set_permission("test", perm)

        assert manager.is_tool_allowed("test", "safe") is True
        assert manager.is_tool_allowed("test", "dangerous") is False

    def test_from_config(self):
        from src.services.mcp.channel_permissions import ChannelPermissionManager

        config = {
            "server1": {
                "allowedTools": ["tool1"],
                "allowAll": False,
            },
            "server2": {
                "allowAll": True,
                "deniedTools": ["danger"],
            },
        }
        manager = ChannelPermissionManager.from_config(config)
        assert len(manager.list_servers()) == 2

    def test_remove_and_clear(self):
        from src.services.mcp.channel_permissions import (
            ChannelPermission,
            ChannelPermissionManager,
        )
        manager = ChannelPermissionManager()
        perm = ChannelPermission(server_name="test")
        manager.set_permission("test", perm)
        assert manager.remove_permission("test") is True
        assert manager.remove_permission("test") is False
        manager.set_permission("test", perm)
        manager.clear()
        assert manager.list_servers() == []

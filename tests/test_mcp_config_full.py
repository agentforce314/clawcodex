import json
import os
import sys
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
        # sys.executable rather than "python": macOS/Linux boxes often ship
        # only `python3`, and shutil.which accepts an absolute path.
        config = McpStdioServerConfig(command=sys.executable)
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


class TestUserScopeConfigWrites:
    """User-scope add/remove target ~/.clawcodex/config.json — the SAME
    file the secret store keeps provider API keys in. Pin the two
    integrity properties the directory rebrand made load-bearing."""

    def _user_home(self, monkeypatch, tmp_path):
        home = tmp_path / "clawhome"
        monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(home))
        return home

    def test_add_preserves_existing_keys_and_uses_0600(self, monkeypatch, tmp_path):
        from src.services.mcp.config import add_mcp_config

        home = self._user_home(monkeypatch, tmp_path)
        home.mkdir(parents=True)
        cfg = home / "config.json"
        cfg.write_text(json.dumps({"providers": {"a": {"api_key": "sk-keep-me"}}}))

        add_mcp_config("srv", {"command": "echo"}, scope="user")

        data = json.loads(cfg.read_text())
        assert data["providers"]["a"]["api_key"] == "sk-keep-me"
        assert data["mcpServers"]["srv"] == {"command": "echo"}
        assert (os.stat(cfg).st_mode & 0o777) == 0o600

    def test_add_raises_on_unreadable_config_without_rewriting(self, monkeypatch, tmp_path):
        from src.services.mcp.config import add_mcp_config

        home = self._user_home(monkeypatch, tmp_path)
        home.mkdir(parents=True)
        cfg = home / "config.json"
        cfg.write_text("{not json — a hand-edit typo")
        before = cfg.read_text()

        with pytest.raises(ValueError, match="cannot be read"):
            add_mcp_config("srv", {"command": "echo"}, scope="user")
        # The corrupt file was NOT replaced with a servers-only skeleton.
        assert cfg.read_text() == before

    def test_remove_roundtrip(self, monkeypatch, tmp_path):
        from src.services.mcp.config import add_mcp_config, remove_mcp_config

        home = self._user_home(monkeypatch, tmp_path)
        add_mcp_config("srv", {"command": "echo"}, scope="user")
        remove_mcp_config("srv", scope="user")
        data = json.loads((home / "config.json").read_text())
        assert data["mcpServers"] == {}

"""#286 — plugin-provided MCP servers participate in the config merge.

`McpPluginWrapper.server_config` (set at registration) flows through
`get_managed_mcp_configs` into the aggregator, the per-name lookup, and
the scope-policy filter like every other scope. Legacy tools-only
registrations stay invisible to the merge.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.plugins.mcp_integration import (
    clear_mcp_plugins,
    wrap_mcp_server_as_plugin,
)
from src.services.mcp.config import (
    filter_mcp_servers_by_policy,
    get_all_mcp_configs,
    get_managed_mcp_configs,
    get_mcp_config_by_name,
)
from src.services.mcp.config import get_mcp_configs_by_scope as _real_by_scope
from src.services.mcp.types import McpStdioServerConfig


@pytest.fixture(autouse=True)
def _fresh_plugin_registry(tmp_path, monkeypatch):
    """Hermetic environment: empty plugin registry, isolated config
    dirs (these are the suite's first end-to-end get_all_mcp_configs
    tests — a developer's real ~/.claude or /etc/claude must not flip
    assertions), and a cleared enterprise-exists cache (a process-wide
    latch nothing else resets)."""
    from src.services.mcp.config import clear_enterprise_config_cache

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("CLAUDE_MANAGED_CONFIG_DIR", str(tmp_path / "managed"))
    clear_enterprise_config_cache()
    clear_mcp_plugins()
    yield
    clear_enterprise_config_cache()
    clear_mcp_plugins()


_CONFIG = McpStdioServerConfig(command="plugin-server", args=["--serve"])


class TestManagedLoader:
    def test_registration_with_config_surfaces_in_managed_scope(self):
        wrap_mcp_server_as_plugin(
            "my-plugin-server", [], server_config=_CONFIG
        )
        managed = get_managed_mcp_configs()
        assert "my-plugin-server" in managed
        scoped = managed["my-plugin-server"]
        assert scoped.scope == "managed"
        assert scoped.config is _CONFIG
        assert scoped.plugin_source == "mcp-my-plugin-server"

    def test_legacy_tools_only_registration_stays_invisible(self):
        wrap_mcp_server_as_plugin("legacy", [{"name": "t"}])
        assert get_managed_mcp_configs() == {}

    def test_server_type_reflected_in_loaded_plugin(self):
        from src.services.mcp.types import McpSSEServerConfig

        wrapper = wrap_mcp_server_as_plugin(
            "sse-server",
            [],
            server_config=McpSSEServerConfig(url="https://example.com/sse"),
        )
        assert wrapper.plugin.mcp_servers == {"sse-server": {"type": "sse"}}


class TestMergeParticipation:
    def test_appears_in_aggregate_and_by_name(self):
        wrap_mcp_server_as_plugin("merged-in", [], server_config=_CONFIG)
        servers, _errors = get_all_mcp_configs()
        assert "merged-in" in servers
        assert servers["merged-in"].scope == "managed"

        scoped = get_mcp_config_by_name("merged-in")
        assert scoped is not None
        assert scoped.scope == "managed"
        assert scoped.config is _CONFIG

    def test_manual_same_name_overrides_plugin(self):
        wrap_mcp_server_as_plugin("shadowed", [], server_config=_CONFIG)
        from src.services.mcp.types import ScopedMcpServerConfig

        manual = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="manual-bin"),
            scope="user",
        )
        def _by_scope(scope):
            if scope == "user":
                return {"shadowed": manual}, []
            return _real_by_scope(scope)

        with patch(
            "src.services.mcp.config.get_mcp_configs_by_scope",
            side_effect=_by_scope,
        ):
            servers, _errors = get_all_mcp_configs()
        assert servers["shadowed"].scope == "user"
        assert servers["shadowed"].config.command == "manual-bin"

    def test_signature_duplicate_of_manual_is_suppressed_with_notice(self):
        # Same launch signature under a DIFFERENT name: the operator's
        # explicit entry wins; the plugin copy is suppressed + noticed.
        wrap_mcp_server_as_plugin(
            "plugin-name",
            [],
            server_config=McpStdioServerConfig(command="same-bin", args=["-x"]),
        )
        from src.services.mcp.types import ScopedMcpServerConfig

        manual = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="same-bin", args=["-x"]),
            scope="user",
        )
        def _by_scope(scope):
            if scope == "user":
                return {"manual-name": manual}, []
            return _real_by_scope(scope)

        with patch(
            "src.services.mcp.config.get_mcp_configs_by_scope",
            side_effect=_by_scope,
        ):
            servers, errors = get_all_mcp_configs()
        assert "manual-name" in servers
        assert "plugin-name" not in servers
        assert any(
            "plugin-name" in e.message and "manual-name" in e.message
            for e in errors
        )


class TestPolicyParticipation:
    def test_allow_managed_only_keeps_plugin_servers(self):
        wrap_mcp_server_as_plugin("kept", [], server_config=_CONFIG)
        managed = get_managed_mcp_configs()
        from src.services.mcp.types import ScopedMcpServerConfig

        user_entry = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="user-bin"), scope="user"
        )
        with patch(
            "src.services.mcp.config._safe_load_settings",
            return_value=SimpleNamespace(
                extra={"allow_managed_only_mcp": True}
            ),
        ):
            filtered, notices = filter_mcp_servers_by_policy(
                {**managed, "user-server": user_entry}
            )
        assert "kept" in filtered
        assert "user-server" not in filtered

    def test_disabled_manual_twin_does_not_suppress_plugin(self):
        # The claudeai-dedup carve-out applies here too: a DISABLED
        # manual entry must not suppress its plugin twin, or disabling
        # the manual copy would leave zero working servers.
        from src.services.mcp.config import set_mcp_server_enabled
        from src.services.mcp.types import ScopedMcpServerConfig

        wrap_mcp_server_as_plugin(
            "plugin-twin",
            [],
            server_config=McpStdioServerConfig(command="twin-bin", args=["-y"]),
        )
        manual = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="twin-bin", args=["-y"]),
            scope="user",
        )

        def _by_scope(scope):
            if scope == "user":
                return {"manual-twin": manual}, []
            return _real_by_scope(scope)

        set_mcp_server_enabled("manual-twin", False)
        try:
            with patch(
                "src.services.mcp.config.get_mcp_configs_by_scope",
                side_effect=_by_scope,
            ):
                servers, _errors = get_all_mcp_configs()
        finally:
            set_mcp_server_enabled("manual-twin", True)
        assert "plugin-twin" in servers


class TestEnterpriseLockdownByName:
    def test_by_name_honors_enterprise_short_circuit(self):
        # When a managed-mcp.json exists, the aggregate returns
        # enterprise-only; the by-name resolve must not be a side door
        # that still hands out plugin configs (#286).
        wrap_mcp_server_as_plugin("locked-out", [], server_config=_CONFIG)
        with patch(
            "src.services.mcp.config._does_enterprise_mcp_config_exist",
            return_value=True,
        ), patch(
            "src.services.mcp.config.get_mcp_configs_by_scope",
            return_value=({}, []),
        ):
            assert get_mcp_config_by_name("locked-out") is None

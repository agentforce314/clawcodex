from __future__ import annotations

from src.permissions.pre_trust import PreTrustDecision
from src.services.mcp import config as mcp_config
from src.services.mcp.types import McpStdioServerConfig, ScopedMcpServerConfig


def _server(scope: str) -> ScopedMcpServerConfig:
    return ScopedMcpServerConfig(
        config=McpStdioServerConfig(command="echo"),
        scope=scope,  # type: ignore[arg-type]
    )


def _patch_mcp_runtime(monkeypatch, *, trusted_project_scopes: bool) -> list[str]:
    calls: list[str] = []

    def fake_scope(scope: str):
        calls.append(scope)
        if scope == "enterprise":
            return {}, []
        return {f"{scope}-server": _server(scope)}, []

    def fake_gate(action: str, *, source: str | None = None, **_kw):
        if source in {"project", "local"} and not trusted_project_scopes:
            return PreTrustDecision(
                allow=False,
                reason=f"{action}: workspace is not trusted for source {source}",
            )
        return PreTrustDecision(allow=True, reason="allowed")

    monkeypatch.setattr(mcp_config, "_does_enterprise_mcp_config_exist", lambda: False)
    monkeypatch.setattr(mcp_config, "get_mcp_configs_by_scope", fake_scope)
    monkeypatch.setattr(mcp_config, "get_managed_mcp_configs", lambda: {})
    monkeypatch.setattr(mcp_config, "get_dynamic_mcp_configs", lambda: {})
    monkeypatch.setattr(
        "src.services.mcp.claudeai.get_cached_claudeai_mcp_configs",
        lambda: {},
    )
    monkeypatch.setattr(
        "src.services.mcp_approval.filter_unapproved_mcpjson_servers",
        lambda servers: (servers, []),
    )
    monkeypatch.setattr(
        "src.permissions.pre_trust.check_pre_trust_gate",
        fake_gate,
    )
    monkeypatch.setattr(
        mcp_config,
        "filter_mcp_servers_by_policy",
        lambda configs: (configs, []),
    )
    return calls


def test_mcp_runtime_skips_project_scopes_before_workspace_trust(monkeypatch):
    calls = _patch_mcp_runtime(monkeypatch, trusted_project_scopes=False)

    configs, errors = mcp_config.get_all_mcp_configs()

    assert set(configs) == {"user-server"}
    assert calls == ["enterprise", "user"]
    assert {error.scope for error in errors} == {"project", "local"}


def test_mcp_runtime_loads_project_scopes_after_workspace_trust(monkeypatch):
    calls = _patch_mcp_runtime(monkeypatch, trusted_project_scopes=True)

    configs, errors = mcp_config.get_all_mcp_configs()

    assert set(configs) == {"user-server", "project-server", "local-server"}
    assert calls == ["enterprise", "user", "project", "local"]
    assert errors == []

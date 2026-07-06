"""Chapter C2 — the _build_runtime INIT ordering (critic C2-MAJOR).

The disabled-server filter was non-functional at the PRIMARY path: at init,
sess._mcp_server_infos() ran BEFORE sess.tool_registry / registry.disabled_servers
were assigned, so a /mcp-disabled server's instructions still reached the
prompt. This drives the REAL _build_runtime with a fake provider + a fake MCP
runtime carrying an enabled + a disabled server, CAPTURES the mcp_servers the
init build actually receives, and asserts the disabled one is excluded — the
exact spot that was inert before, tested through the live wiring (not the unit).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.server.agent_server import (
    AgentServerConfig,
    _AgentSession,
    _build_runtime,
)
from src.tool_system.defaults import build_default_registry


class _FakeProvider:
    def __init__(self, api_key=None, base_url=None, model=None):
        self.model = model or "fake"


def _srv(name):
    return SimpleNamespace(name=name, type="connected", instructions=f"use {name}")


def test_init_build_filters_disabled_server_instructions(tmp_path):
    captured = {}

    def _capture_prompt(*a, **k):
        captured["mcp_servers"] = k.get("mcp_servers")
        return "You are a test assistant."

    registry = build_default_registry()

    class _FakeRuntime:
        def __init__(self):
            self.server_infos = [_srv("enabled"), _srv("disabled")]
            self.tools = [SimpleNamespace(name="mcp__enabled__t")]
            self.clients = {"enabled": object(), "disabled": object()}
            self.servers = {"enabled": ["t"], "disabled": ["u"]}

        def start(self):
            return True

        def shutdown(self):
            pass

    sess = _AgentSession(
        session_id="s1",
        cwd=str(tmp_path),
        config=AgentServerConfig(provider_name="anthropic", single_session=True),
        loop=MagicMock(),
        out_queue=MagicMock(),
    )

    with patch("src.config.get_default_provider", lambda: "anthropic"), \
         patch("src.config.get_provider_config",
               lambda n: {"api_key": "x", "default_model": "fake", "base_url": None}), \
         patch("src.providers.get_provider_class", lambda n: _FakeProvider), \
         patch("src.providers.provider_requires_api_key", lambda n: False), \
         patch("src.providers.resolve_api_key", lambda n, c: "x"), \
         patch("src.tool_system.defaults.build_default_registry",
               lambda provider=None: registry), \
         patch("src.query.agent_loop_compat.build_effective_system_prompt",
               _capture_prompt), \
         patch("src.outputStyles.resolve_output_style",
               lambda *a, **k: SimpleNamespace(prompt="")), \
         patch("src.server.mcp_runtime.McpRuntime", _FakeRuntime), \
         patch("src.server.agent_server._load_disabled_mcp", lambda: {"disabled"}):
        _build_runtime(sess, None)

    # the init build must have RECEIVED the infos (not None), with the
    # disabled server filtered out — this fails if the registry/disabled set
    # is assigned AFTER the build (the ordering bug).
    infos = captured.get("mcp_servers")
    assert infos is not None, "init build got mcp_servers=None (wiring inert)"
    names = {getattr(s, "name", None) for s in infos}
    assert names == {"enabled"}, f"disabled server not filtered at init: {names}"

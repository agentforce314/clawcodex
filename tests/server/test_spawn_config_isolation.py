"""Each spawned session owns its AgentServerConfig.

The bug this pins: ``make_spawn_agent`` handed the SAME config object to
every session it spawned, and a cross-provider model switch writes that
object (``_install_provider``: ``cfg.provider_name = name; cfg.model =
model``). Switch any session's provider and the process-wide spawn template
was rewritten — every later bare ``session.create`` resolved
``cfg.provider_name or get_default_provider()`` to the SWITCHED provider,
silently overriding the user's ``default_provider`` until the server was
restarted. Observed live: with ``default_provider: deepseek`` on disk, a
bare create returned deepseek before a picker switch to openai and
openai:gpt-5.6-luna forever after it.
"""

from __future__ import annotations

import asyncio

import pytest

from src.server import agent_server as mod
from src.server.agent_server import AgentServerConfig, make_spawn_agent

pytestmark = pytest.mark.integration


class _FakeSession:
    """Stands in for _AgentSession: records the config it was handed."""

    captured: list[AgentServerConfig] = []

    def __init__(self, **kwargs) -> None:
        _FakeSession.captured.append(kwargs["config"])
        self.config = kwargs["config"]
        # The attributes spawn() touches after construction.
        self.tool_context = None
        self.init_error = None

    def start(self) -> None:
        pass

    def emit_init(self) -> None:
        pass

    async def send_to_agent(self, msg) -> None:
        pass

    async def shutdown(self) -> None:
        pass


def test_each_spawn_owns_its_config(monkeypatch) -> None:
    _FakeSession.captured = []
    monkeypatch.setattr(mod, "_AgentSession", _FakeSession)
    monkeypatch.setattr(mod, "_build_runtime", lambda sess, perm: None)

    base = AgentServerConfig()
    spawn = make_spawn_agent(base)

    async def two() -> None:
        await spawn("a", "/tmp/w", None)
        await spawn("b", "/tmp/w", None)

    asyncio.run(two())

    cfg_a, cfg_b = _FakeSession.captured
    assert cfg_a is not base and cfg_b is not base and cfg_a is not cfg_b
    # Values still come from the template.
    assert cfg_a == base and cfg_b == base

    # What _install_provider does on a cross-provider switch: session A's
    # write must reach neither the template nor a sibling session.
    cfg_a.provider_name = "openai"
    cfg_a.model = "gpt-5.6-luna"

    assert base.provider_name is None and base.model is None
    assert cfg_b.provider_name is None and cfg_b.model is None

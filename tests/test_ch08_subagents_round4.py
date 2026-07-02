"""ch08 round-4 acceptance tests: per-subagent model resolution, query_source
labeling, and the bubble self-consistency + CLI guard.

Covers my-docs/port-improvement-round-4/ch08-subagents-round4-plan.md.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.agent.agent_model import get_agent_model
from src.agent.agent_tool_utils import get_query_source_for_agent


class _FakeProvider:
    def __init__(self, model, available):
        self.model = model
        self._available = available

    def get_available_models(self):
        return list(self._available)


_SONNET = "claude-sonnet-4-20250514"
_HAIKU = "claude-3-5-haiku-20241022"


class TestAgentModelResolution(unittest.TestCase):
    def setUp(self):
        # Ensure no env override leaks between tests.
        self._env = dict(os.environ)
        os.environ.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_inherit_returns_session_model(self):
        p = _FakeProvider(_SONNET, [_SONNET, _HAIKU])
        self.assertEqual(get_agent_model(None, "inherit", p), _SONNET)
        self.assertEqual(get_agent_model(None, None, p), _SONNET)

    def test_alias_resolves_when_available(self):
        p = _FakeProvider(_SONNET, [_SONNET, _HAIKU])
        self.assertEqual(get_agent_model(None, "haiku", p), _HAIKU)

    def test_same_tier_alias_keeps_parent_exact_model(self):
        # critic M2 — session is a NEWER same-tier model; 'sonnet' must keep
        # it, NOT downgrade to the alias's canonical (older) target.
        p = _FakeProvider("claude-sonnet-4-6", [_SONNET, _HAIKU])
        self.assertEqual(get_agent_model(None, "sonnet", p), "claude-sonnet-4-6")
        self.assertEqual(get_agent_model("sonnet", None, p), "claude-sonnet-4-6")

    def test_cross_tier_alias_downgrades_as_requested(self):
        # A DIFFERENT tier is honored (opus session, 'haiku' → haiku).
        p = _FakeProvider("claude-opus-4-6", [_SONNET, _HAIKU, "claude-opus-4-6"])
        self.assertEqual(get_agent_model(None, "haiku", p), _HAIKU)

    def test_full_id_trusted_literally(self):
        # critic M3 — an explicit full id is trusted even if absent from the
        # (aging) static list, so proxy/custom-name deployments work.
        p = _FakeProvider(_SONNET, [_SONNET])
        self.assertEqual(
            get_agent_model("claude-opus-4-7", None, p), "claude-opus-4-7",
        )

    def test_tool_model_overrides_agent_def(self):
        p = _FakeProvider(_SONNET, [_SONNET, _HAIKU])
        # tool 'haiku' wins over agent-def 'sonnet'
        self.assertEqual(get_agent_model("haiku", "sonnet", p), _HAIKU)

    def test_unavailable_alias_falls_back_to_session(self):
        # A DeepSeek-style provider that doesn't serve 'haiku' → inherit,
        # never a foreign model that would 400.
        p = _FakeProvider("deepseek-v4-pro", ["deepseek-v4-pro"])
        self.assertEqual(get_agent_model(None, "haiku", p), "deepseek-v4-pro")

    def test_env_override_wins(self):
        p = _FakeProvider(_SONNET, [_SONNET, _HAIKU])
        os.environ["CLAUDE_CODE_SUBAGENT_MODEL"] = "haiku"
        self.assertEqual(get_agent_model("sonnet", "sonnet", p), _HAIKU)

    def test_env_override_full_id_trusted(self):
        p = _FakeProvider(_SONNET, [_SONNET])
        os.environ["CLAUDE_CODE_SUBAGENT_MODEL"] = "my-proxy-model-x"
        self.assertEqual(get_agent_model(None, None, p), "my-proxy-model-x")

    def test_provider_enumeration_failure_inherits(self):
        class _Broken:
            model = _SONNET

            def get_available_models(self):
                raise RuntimeError("boom")

        self.assertEqual(get_agent_model(None, "haiku", _Broken()), _SONNET)

    def test_never_raises(self):
        # Even a totally bogus provider returns something falsy-safe.
        class _Nothing:
            pass

        self.assertEqual(get_agent_model(None, "haiku", _Nothing()), "")


class TestModelResolutionIsConcurrencySafe(unittest.TestCase):
    """The resolver must NOT mutate the shared session provider (ch07 made
    Agent concurrency-safe → parallel subagents share the provider)."""

    def test_resolution_does_not_mutate_provider(self):
        p = _FakeProvider(_SONNET, [_SONNET, _HAIKU])
        resolved = get_agent_model(None, "haiku", p)
        self.assertEqual(resolved, _HAIKU)
        # The shared provider's model is UNCHANGED — the caller clones.
        self.assertEqual(p.model, _SONNET)


class TestExploreRunsOnHaiku(unittest.TestCase):
    def test_explore_agent_def_declares_haiku(self):
        # critic M1 — the headline win: Explore's built-in def must request
        # haiku so get_agent_model resolves it (on Anthropic sessions).
        from src.agent.agent_definitions import EXPLORE_AGENT

        self.assertEqual(EXPLORE_AGENT.model, "haiku")

    def test_explore_resolves_to_haiku_on_anthropic_session(self):
        p = _FakeProvider(_SONNET, [_SONNET, _HAIKU])
        from src.agent.agent_definitions import EXPLORE_AGENT

        self.assertEqual(get_agent_model(None, EXPLORE_AGENT.model, p), _HAIKU)


class TestRunAgentClonesProvider(unittest.TestCase):
    """critic minor — the ACTUAL mutation site: run_agent must clone the
    provider and set the resolved model on the clone, never on the shared
    session provider."""

    def test_run_agent_uses_cloned_provider(self):
        import asyncio
        from unittest.mock import patch

        from src.agent.run_agent import RunAgentParams, run_agent
        from src.agent.agent_definitions import EXPLORE_AGENT

        session_provider = _FakeProvider(_SONNET, [_SONNET, _HAIKU])
        captured = {}

        async def _fake_query(qp):
            captured["provider"] = qp.provider
            captured["model"] = getattr(qp.provider, "model", None)
            return
            yield  # make it an async generator

        params = RunAgentParams(
            parent_context=_min_context(),
            agent_definition=EXPLORE_AGENT,
            prompt="explore",
            available_tools=[],
            tool_registry=_min_registry(),
            provider=session_provider,
        )
        # run_agent imports `query` inside the function from src.query.query.
        with patch("src.query.query.query", _fake_query):
            asyncio.run(_drain(run_agent(params)))

        # The query got a clone carrying haiku…
        self.assertEqual(captured.get("model"), _HAIKU)
        self.assertIsNot(captured.get("provider"), session_provider)
        # …and the SHARED session provider is unmutated (concurrency-safe).
        self.assertEqual(session_provider.model, _SONNET)


async def _drain(agen):
    async for _ in agen:
        pass


def _min_context():
    from pathlib import Path

    from src.tool_system.context import ToolContext, ToolUseOptions

    ctx = ToolContext(workspace_root=Path("/tmp"))
    ctx.options = ToolUseOptions(tools=[])
    return ctx


def _min_registry():
    from src.tool_system.defaults import build_default_registry

    return build_default_registry()


class TestQuerySourceLabeling(unittest.TestCase):
    def test_builtin_label(self):
        self.assertEqual(
            get_query_source_for_agent("Explore", True),
            "agent:builtin:Explore",
        )

    def test_custom_label(self):
        self.assertEqual(
            get_query_source_for_agent("my-agent", False), "agent:custom",
        )


class TestBubbleAndCliGuards(unittest.TestCase):
    def test_bubble_surfaces_ask(self):
        from src.permissions.check import has_permissions_to_use_tool
        from src.permissions.types import ToolPermissionContext
        from src.tool_system.build_tool import build_tool

        tool = build_tool(
            name="TestTool", input_schema={"type": "object"},
            call=lambda i, c: None, prompt="", description="",
        )
        ctx = ToolPermissionContext(mode="bubble")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "ask")

    def test_agent_server_cli_rejects_bubble(self):
        from src.entrypoints.agent_server_cli import run_agent_server_subcommand

        # The guard returns 2 BEFORE any server spawn.
        rc = run_agent_server_subcommand(["--permission-mode", "bubble", "--stdio"])
        self.assertEqual(rc, 2)

    def test_tui_launcher_rejects_bubble(self):
        from src.entrypoints.tui_launcher import run_tui_launcher

        rc = run_tui_launcher(["--permission-mode", "bubble"])
        self.assertEqual(rc, 2)

    def test_bubble_headless_fails_closed(self):
        # critic minor — the untested safety branch: bubble + no prompts →
        # deny (fail-closed).
        from src.permissions.check import has_permissions_to_use_tool
        from src.permissions.types import ToolPermissionContext
        from src.tool_system.build_tool import build_tool

        tool = build_tool(
            name="TestTool", input_schema={"type": "object"},
            call=lambda i, c: None, prompt="", description="",
        )
        ctx = ToolPermissionContext(
            mode="bubble", should_avoid_permission_prompts=True,
        )
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "deny")

    def test_cli_still_accepts_auto(self):
        # critic minor — lock intent: 'auto' (ch06 classifier lane) must NOT
        # be rejected. run_tui_launcher with auto should pass the guard (it
        # won't return 2 for the permission-mode reason). We stop it before
        # the real launch by giving --print-connect a path it handles.
        from unittest.mock import patch

        from src.entrypoints import tui_launcher

        with patch.object(tui_launcher, "_print_connect", return_value=0):
            rc = tui_launcher.run_tui_launcher(
                ["--permission-mode", "auto", "--print-connect"],
            )
        self.assertEqual(rc, 0)  # not the bubble-reject 2


if __name__ == "__main__":
    unittest.main()

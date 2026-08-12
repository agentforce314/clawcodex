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


# Current live ids (2026-08 refresh) — the retired 2025 ids these fixtures
# used to pin were removed from the API, and the alias table now targets
# these. ``_FakeProvider`` carries no ``provider_id``, so these tests
# exercise the provider-table-less MECHANISM (global aliases + availability
# gate); the per-provider table behavior is covered by
# ``TestPerProviderSubagentDefaults`` below.
_SONNET = "claude-sonnet-5"
_HAIKU = "claude-haiku-4-5"


class TestAgentModelResolution(unittest.TestCase):
    def setUp(self):
        # Ensure no env override leaks between tests.
        self._env = dict(os.environ)
        os.environ.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
        for var in (
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        ):
            os.environ.pop(var, None)

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


class TestPerProviderSubagentDefaults(unittest.TestCase):
    """The per-provider subagent tables (PROVIDER_INFO ``subagent_model`` /
    ``subagent_tier_models``) — the 2026-08 fix for subagents 404-ing on
    retired first-party ids and DeepSeek fan-outs silently billing the
    expensive session model."""

    def setUp(self):
        self._env = dict(os.environ)
        for var in (
            "CLAUDE_CODE_SUBAGENT_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_BASE_URL",
        ):
            os.environ.pop(var, None)
        # Hermetic: the resolver consults the user's real config.json for
        # the ``providers.<id>.subagent_model`` knob — neutralize it.
        self._cfg = patch("src.config.get_provider_config", return_value={})
        self._cfg.start()

    def tearDown(self):
        self._cfg.stop()
        os.environ.clear()
        os.environ.update(self._env)

    @staticmethod
    def _anthropic(model="claude-fable-5", **kwargs):
        from src.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key="test-key", model=model, **kwargs)

    @staticmethod
    def _deepseek(model="deepseek-v4-pro"):
        from src.providers.deepseek_provider import DeepSeekProvider

        return DeepSeekProvider(api_key="test-key", model=model)

    def test_anthropic_unspecified_uses_default_subagent_model(self):
        # Goal ask #1: on the anthropic provider the default subagent model
        # is claude-haiku-4-5 (the cheapest current-gen tier), NOT an
        # inherit of the (pricier) session model.
        p = self._anthropic()
        self.assertEqual(get_agent_model(None, None, p), "claude-haiku-4-5")

    def test_anthropic_haiku_tier_resolves_to_live_haiku(self):
        # The screenshot bug: Explore pins 'haiku', whose old alias target
        # (claude-3-5-haiku-20241022) was retired and 404'd every spawn.
        # The bare claude-haiku-4-5 resolves server-side (probed live).
        p = self._anthropic()
        self.assertEqual(
            get_agent_model(None, "haiku", p), "claude-haiku-4-5",
        )

    def test_deepseek_unspecified_uses_flash(self):
        # Goal ask #2: deepseek-v4-flash is the subagent default.
        p = self._deepseek()
        self.assertEqual(get_agent_model(None, None, p), "deepseek-v4-flash")

    def test_deepseek_haiku_tier_uses_flash(self):
        # Previously 'haiku' fell back to inherit → every Explore fan-out
        # ran (and billed) the v4-pro session model.
        p = self._deepseek()
        self.assertEqual(get_agent_model(None, "haiku", p), "deepseek-v4-flash")

    def test_deepseek_opus_tier_uses_pro(self):
        p = self._deepseek(model="deepseek-v4-flash")
        self.assertEqual(get_agent_model("opus", None, p), "deepseek-v4-pro")

    def test_explicit_inherit_still_forces_session_model(self):
        # The Plan/fork agents pin 'inherit' — the provider default must
        # not override an explicit inherit.
        self.assertEqual(
            get_agent_model(None, "inherit", self._anthropic()),
            "claude-fable-5",
        )
        self.assertEqual(
            get_agent_model("inherit", None, self._deepseek()),
            "deepseek-v4-pro",
        )

    def test_custom_anthropic_endpoint_inherits(self):
        # TS checkIsClaudeNativeProvider: a proxy/self-hosted endpoint has
        # no guaranteed first-party catalog — the table is off, and the
        # haiku/sonnet aliases inherit rather than resolve to first-party
        # ids the proxy may not serve.
        p = self._anthropic(
            model="my-proxy-model", base_url="https://proxy.example/v1",
        )
        self.assertEqual(get_agent_model(None, None, p), "my-proxy-model")
        self.assertEqual(get_agent_model(None, "haiku", p), "my-proxy-model")
        self.assertEqual(get_agent_model("sonnet", None, p), "my-proxy-model")

    def _set_config_knob(self, value):
        self._cfg.stop()
        self._cfg = patch(
            "src.config.get_provider_config",
            return_value={"subagent_model": value},
        )
        self._cfg.start()

    def test_config_subagent_model_knob_wins_and_is_trusted(self):
        # providers.<id>.subagent_model (the opencode ``small_model`` knob)
        # beats the registry default; a full id bypasses the availability
        # gate (the user is naming a deployment).
        self._set_config_knob("my-finetuned-small")
        p = self._anthropic()
        self.assertEqual(get_agent_model(None, None, p), "my-finetuned-small")

    def test_config_knob_inherit_restores_session_model(self):
        # critic B1 — 'inherit' is the natural "stop downgrading my
        # subagents" spelling; it must resolve to the session model, never
        # go on the wire as a literal model id.
        self._set_config_knob("inherit")
        p = self._anthropic()
        self.assertEqual(get_agent_model(None, None, p), "claude-fable-5")

    def test_config_knob_alias_resolves_through_tier_table(self):
        # critic B1 — a bare tier alias in the knob resolves like any other
        # user-specified alias instead of shipping the raw string.
        self._set_config_knob("sonnet")
        p = self._anthropic()
        self.assertEqual(get_agent_model(None, None, p), "claude-sonnet-5")

    def test_markdown_agent_without_model_gets_provider_default(self):
        # critic M4 — a user-authored .md agent with no ``model:``
        # frontmatter parses to model=None and takes the provider default.
        from src.agent.parse_agent_markdown import parse_agent_from_markdown

        agent = parse_agent_from_markdown(
            "/tmp/my-agent.md",
            {"name": "my-agent", "description": "d"},
            "Body",
            "user",
            "/tmp",
        )
        self.assertIsNotNone(agent)
        self.assertIsNone(agent.model)
        self.assertEqual(
            get_agent_model(None, agent.model, self._anthropic()),
            "claude-haiku-4-5",
        )

    def test_coordinator_mode_pins_inherit_at_the_tool_layer(self):
        # critic B2 — coordinator workers cannot pass a model param (the
        # tool layer discards it), so that path pins 'inherit': workers do
        # implementation work and must stay on the session model.
        self.assertEqual(
            get_agent_model("inherit", None, self._anthropic()),
            "claude-fable-5",
        )
        import inspect

        from src.tool_system.tools import agent as agent_tool_module

        src_text = inspect.getsource(agent_tool_module)
        self.assertIn(
            'model = "inherit" if is_coordinator_mode()', src_text,
            "coordinator path must pin 'inherit', not None — None now "
            "resolves to the provider's cheap default subagent model",
        )

    def test_registry_subagent_targets_are_available(self):
        # critic M5 — the availability gate silently degrades a stale
        # registry row to inherit, so a typo in a future row would be an
        # invisible no-op. Pin the invariant against the list the RUNTIME
        # gate actually reads — the provider INSTANCE's
        # get_available_models(), a separately-maintained literal from
        # PROVIDER_INFO's available_models — and against the registry list
        # too, so drift in either direction fails here rather than
        # silently no-oping in production.
        from src.providers import PROVIDER_INFO, get_provider_class

        checked = 0
        for provider_id, info in PROVIDER_INFO.items():
            targets = []
            default = info.get("subagent_model")
            if default:
                targets.append(("subagent_model", default))
            for tier, model in (info.get("subagent_tier_models") or {}).items():
                targets.append((f"tier:{tier}", model))
            if not targets:
                continue
            checked += 1
            registry_list = info.get("available_models") or []
            instance = get_provider_class(provider_id)(
                api_key="test-key", model=None,
            )
            runtime_list = instance.get_available_models()
            for label, target in targets:
                for name, available in (
                    ("PROVIDER_INFO available_models", registry_list),
                    ("get_available_models()", runtime_list),
                ):
                    self.assertIn(
                        target, available,
                        f"{provider_id} {label} names {target!r}, which its "
                        f"{name} does not list — the runtime gate would "
                        "silently ignore it",
                    )
        # Today: anthropic + deepseek. If this drops to zero the tables were
        # deleted and this test should go with them.
        self.assertGreaterEqual(checked, 2)

    def test_tier_env_pin_beats_table(self):
        os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = "my-bedrock-haiku"
        p = self._anthropic()
        self.assertEqual(get_agent_model(None, "haiku", p), "my-bedrock-haiku")

    def test_tier_env_pin_does_not_leak_to_other_providers(self):
        # critic r4 — the ANTHROPIC_* env pins name Anthropic deployments;
        # honoring one on a DeepSeek session would ship that id to
        # api.deepseek.com on every Explore spawn (hard 400).
        os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = "my-bedrock-haiku"
        d = self._deepseek()
        self.assertEqual(get_agent_model(None, "haiku", d), "deepseek-v4-flash")

    def test_known_alias_spellings_never_ship_raw(self):
        # critic r3 — trust_literal must only trust ids NO alias table
        # knows. 's4', 'claude-4-sonnet', 'claude-haiku' are alias
        # spellings, not servable model ids: they resolve to their
        # canonical target (and take the availability gate), never go on
        # the wire verbatim.
        p = self._anthropic()
        self.assertEqual(
            get_agent_model("claude-4-sonnet", None, p), "claude-sonnet-4-6",
        )
        self.assertEqual(
            get_agent_model("claude-haiku", None, p), "claude-haiku-4-5",
        )
        os.environ["CLAUDE_CODE_SUBAGENT_MODEL"] = "s4"
        try:
            resolved = get_agent_model(None, None, p)
        finally:
            os.environ.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
        self.assertEqual(resolved, "claude-sonnet-4-6")

    def test_alias_of_retired_model_degrades_to_inherit(self):
        # The legacy pins ('claude-3.5-haiku', 'h35') canonicalize to ids
        # the live catalog no longer serves; post-prune the availability
        # gate degrades them to inherit instead of shipping a 404.
        p = self._anthropic()
        self.assertEqual(
            get_agent_model("claude-3.5-haiku", None, p), "claude-fable-5",
        )
        self.assertEqual(get_agent_model("h35", None, p), "claude-fable-5")

    def test_alias_unservable_on_provider_inherits(self):
        # A known alias whose canonical target the session provider does
        # not serve degrades to inherit (never the raw spelling, never a
        # foreign id that would 400 louder).
        d = self._deepseek()
        self.assertEqual(
            get_agent_model("claude-haiku", None, d), "deepseek-v4-pro",
        )

    def test_same_tier_alias_still_keeps_parent_exact_model(self):
        # M2 precedes the table: a sonnet-tier session asked for 'sonnet'
        # keeps its exact (possibly older) model — no surprise upgrade to
        # the table's claude-sonnet-5.
        p = self._anthropic(model="claude-sonnet-4-6")
        self.assertEqual(get_agent_model("sonnet", None, p), "claude-sonnet-4-6")

    def test_stale_table_row_degrades_to_inherit(self):
        # If a registry row ever names a model the provider's catalog does
        # not list, degrade to inherit instead of shipping a 404.
        p = self._anthropic()
        with patch.object(
            type(p), "get_available_models", return_value=["claude-fable-5"],
        ):
            self.assertEqual(get_agent_model(None, None, p), "claude-fable-5")
            self.assertEqual(get_agent_model(None, "haiku", p), "claude-fable-5")

    def test_general_purpose_and_explore_defs_route_as_designed(self):
        # End-to-end over the built-in defs: general-purpose (no model) →
        # provider default; Explore (haiku) → provider haiku tier.
        from src.agent.agent_definitions import EXPLORE_AGENT, GENERAL_PURPOSE_AGENT

        a = self._anthropic()
        d = self._deepseek()
        self.assertEqual(
            get_agent_model(None, GENERAL_PURPOSE_AGENT.model, a),
            "claude-haiku-4-5",
        )
        self.assertEqual(
            get_agent_model(None, EXPLORE_AGENT.model, a),
            "claude-haiku-4-5",
        )
        self.assertEqual(
            get_agent_model(None, GENERAL_PURPOSE_AGENT.model, d),
            "deepseek-v4-flash",
        )
        self.assertEqual(
            get_agent_model(None, EXPLORE_AGENT.model, d), "deepseek-v4-flash",
        )


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

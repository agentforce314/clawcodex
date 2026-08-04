"""Reasoning-effort wiring for OpenAI-compatible providers, plus the
OpenRouter-qualified model-config lookup that GPT-5.6 Luna needs.

Background — the bugs these lock down.

1. The two provider families take reasoning effort as different wire
   parameters (``output_config.effort`` on the Anthropic wire, a top-level
   ``reasoning_effort`` body field on the OpenAI-compatible one).
   ``_call_model_sync`` emitted it only inside its ``is_anthropic`` branch,
   so on the headless ``-p`` path — the one the terminal-bench harness
   drives — ``--effort`` was a SILENT no-op for every OpenAI-compatible
   provider. Captured against a local capture server on 2026-07-31,
   ``--effort max --provider openrouter`` produced a request body of
   ``{messages, model, stream, stream_options, tools}``: no effort field of
   any kind, no error, no log line.

2. Fixing (1) then collided with ``_AgentSession._turn_effort_routing``,
   which wrapped OpenAI-compatible providers in an ``_EffortProvider`` that
   injected the same field with ``setdefault``. Routing passed
   ``thinking_effort=None`` for that family, so query.py filled the key from
   ``settings.effort`` first and the wrapper's ``setdefault`` no-op'd: an
   explicit session ``/effort max`` went out as ``medium``, inverting the
   documented precedence. The wrapper is now deleted — one level, one
   injection site.

3. ``MODEL_CONFIGS`` is keyed by BARE model name, so ``openai/gpt-5.6-luna``
   — the id that actually reaches the provider on the OpenRouter path —
   matched nothing and fell back to ``DEFAULT_CONTEXT_WINDOW`` (200K)
   against a real 1M-class window, i.e. auto-compact at a fifth of capacity.
   Fixed with one explicit vendor-qualified row, NOT by teaching the resolver
   to strip ``<vendor>/`` (that is "decision #1" and stays out of scope).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

from src.models.configs import MODEL_CONFIGS, get_model_config
from src.models.context import (
    DEFAULT_CONTEXT_WINDOW,
    get_context_window_for_model,
    get_model_max_output_tokens,
)
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.base import ChatResponse
from src.providers.openrouter_provider import OpenRouterProvider
from src.query.query import QueryParams, query
from src.services.pricing import compute_cost, get_pricing
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController

LUNA = "openai/gpt-5.6-luna"


def _run(coro):
    return asyncio.run(coro)


def _no_settings_effort():
    """Pin ``settings.effort`` empty so a developer's own configured effort
    can't mask an assertion that the parameter is omitted."""
    return mock.patch(
        "src.settings.settings.get_settings",
        return_value=SimpleNamespace(effort=""),
    )


def _make_openrouter_mock(model: str = LUNA) -> MagicMock:
    """A mock that fails ``is_anthropic_wire`` — i.e. takes the
    OpenAI-compatible branch. Streaming is forced into the ``chat()``
    fallback so assertions can read kwargs off ``chat.call_args``."""
    provider = MagicMock(spec=OpenRouterProvider)
    provider.model = model
    provider.base_url = "https://openrouter.ai/api/v1"
    provider.chat_stream_response.side_effect = NotImplementedError()
    provider.chat.return_value = ChatResponse(
        content="ok",
        model=model,
        usage={"input_tokens": 1, "output_tokens": 1},
        finish_reason="stop",
        tool_uses=None,
    )
    return provider


def _make_anthropic_mock(model: str) -> MagicMock:
    provider = MagicMock(spec=AnthropicProvider)
    provider.model = model
    provider.chat_stream_response.side_effect = NotImplementedError()
    provider.chat.return_value = ChatResponse(
        content="ok",
        model=model,
        usage={"input_tokens": 1, "output_tokens": 1},
        finish_reason="end_turn",
        tool_uses=None,
    )
    return provider


class TestOpenAICompatEffortOnTheWire(unittest.TestCase):
    """Drive one real turn and inspect what the provider actually received."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=Path(self.tmp.name))
        self.abort = AbortController()

    def tearDown(self):
        self.tmp.cleanup()

    def _drive_one_turn(self, provider: MagicMock, **extra) -> dict:
        params = QueryParams(
            messages=[UserMessage(content="hi")],
            system_prompt="hello",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=1,
            **extra,
        )

        async def run():
            async for _ in query(params):
                pass

        _run(run())
        self.assertTrue(provider.chat.called, "provider.chat() should have run")
        return provider.chat.call_args.kwargs

    def test_effort_max_reaches_the_wire(self):
        """The reported bug: --effort max must actually ship."""
        with _no_settings_effort():
            kw = self._drive_one_turn(
                _make_openrouter_mock(), thinking_effort="max"
            )
        self.assertEqual(
            (kw.get("extra_body") or {}).get("reasoning_effort"),
            "max",
            "reasoning_effort must be on the OpenAI-compatible body",
        )

    def test_each_level_ships_verbatim(self):
        for level in ("low", "medium", "high", "max"):
            with self.subTest(level=level), _no_settings_effort():
                kw = self._drive_one_turn(
                    _make_openrouter_mock(), thinking_effort=level
                )
                self.assertEqual(
                    (kw.get("extra_body") or {}).get("reasoning_effort"), level
                )

    def test_xhigh_is_not_clamped_on_this_wire(self):
        """``_model_supports_xhigh_effort`` is an allowlist of ANTHROPIC model
        names matched by substring, so it matches nothing here and would
        downgrade every xhigh to high. xhigh is a first-class OpenAI level
        (and the one their guide recommends for long agentic runs)."""
        with _no_settings_effort():
            kw = self._drive_one_turn(
                _make_openrouter_mock(), thinking_effort="xhigh"
            )
        self.assertEqual(
            (kw.get("extra_body") or {}).get("reasoning_effort"), "xhigh"
        )

    def test_xhigh_still_clamped_on_the_anthropic_wire(self):
        """The clamp must stay on where an unsupported xhigh is a hard 400."""
        with _no_settings_effort():
            kw = self._drive_one_turn(
                _make_anthropic_mock("claude-sonnet-4-6"), thinking_effort="xhigh"
            )
        self.assertEqual(kw.get("output_config"), {"effort": "high"})

    def test_output_config_never_sent_on_this_wire(self):
        """``output_config`` is the Anthropic shape; sending it here is the
        mirror-image of the 400 that motivated the interactive split."""
        with _no_settings_effort():
            kw = self._drive_one_turn(
                _make_openrouter_mock(), thinking_effort="max"
            )
        self.assertNotIn("output_config", kw)
        self.assertNotIn("thinking", kw)

    def test_absent_when_no_effort_requested(self):
        """The default path must be byte-identical to before the fix."""
        with _no_settings_effort():
            kw = self._drive_one_turn(_make_openrouter_mock())
        self.assertNotIn("reasoning_effort", kw.get("extra_body") or {})

    def test_settings_effort_is_honored(self):
        """No flag, but a persisted ``settings.effort`` — the same source the
        harness seeds into a container so subagents inherit the level."""
        with mock.patch(
            "src.settings.settings.get_settings",
            return_value=SimpleNamespace(effort="max"),
        ):
            kw = self._drive_one_turn(_make_openrouter_mock())
        self.assertEqual(
            (kw.get("extra_body") or {}).get("reasoning_effort"), "max"
        )

    def test_anthropic_still_uses_output_config(self):
        """The other half of the routing split must not regress: Anthropic
        keeps ``output_config`` and must never grow ``reasoning_effort``,
        which that wire rejects with a hard 400."""
        with _no_settings_effort():
            kw = self._drive_one_turn(
                _make_anthropic_mock("claude-opus-4-8"), thinking_effort="max"
            )
        self.assertEqual(kw.get("output_config"), {"effort": "max"})
        self.assertNotIn("reasoning_effort", kw.get("extra_body") or {})


class TestLunaOpenRouterIdRegistration(unittest.TestCase):
    """The bare ``gpt-5.6-*`` family is registered elsewhere (#773). What is
    pinned here is only the OpenRouter-qualified id, which is what actually
    reaches the provider when the terminal-bench harness runs this model."""

    def test_openrouter_id_resolves_to_the_real_window(self):
        self.assertEqual(get_context_window_for_model(LUNA), 1_048_576)
        self.assertEqual(get_model_max_output_tokens(LUNA), 128_000)

    def test_not_the_200k_default(self):
        """The specific failure mode: silently sized at 200K, auto-compacting
        at a fifth of the model's real capacity."""
        self.assertNotEqual(
            get_context_window_for_model(LUNA), DEFAULT_CONTEXT_WINDOW
        )

    def test_qualified_and_bare_agree(self):
        """A model must not have two different windows depending on which
        gateway routed it."""
        self.assertEqual(
            get_context_window_for_model(LUNA),
            get_context_window_for_model("gpt-5.6-luna"),
        )

    def test_pro_variant_resolves_through_the_prefix_fallback(self):
        for model_id in ("openai/gpt-5.6-luna-pro", "gpt-5.6-luna-pro"):
            with self.subTest(model=model_id):
                self.assertEqual(
                    get_context_window_for_model(model_id), 1_048_576
                )

    def test_decision_1_upheld_no_vendor_prefix_stripping(self):
        """``get_model_config`` must NOT strip a ``<vendor>/`` prefix. Pinned
        by tests/test_deepseek_prefix_cache.py as "decision #1"; duplicated
        here because adding a vendor-qualified row is exactly the change that
        tempts someone to generalize it into a resolver tier."""
        self.assertEqual(
            get_context_window_for_model("deepseek/deepseek-v4-pro"), 200_000
        )
        self.assertIsNone(get_model_config("anthropic/claude-sonnet-4.5"))
        self.assertIsNone(get_model_config("openai/gpt-4o"))

    def test_the_new_row_does_not_perturb_other_ids(self):
        """The added key claims prefix "openai/gpt-5.6". Nothing else may
        start resolving through it. Hardcoded expectations rather than a
        reimplementation of the resolver, so this cannot degenerate into a
        tautology."""
        expected = {
            # Inside the claimed prefix — these DO now resolve, to the same
            # window their bare equivalents get from #773's rows. Pinned so
            # the size of the claim is a fact rather than a comment.
            "openai/gpt-5.6-mini": 1_048_576,
            "openai/gpt-5.6-sol": 1_048_576,
            # Outside it — must stay unresolved.
            "openai/gpt-4o": None,
            "openai/gpt-5.5": None,
            "openai/gpt-5.4-mini": None,
            "openai/o1": None,
            "anthropic/claude-opus-4-8": None,
            "deepseek/deepseek-v4-flash": None,
            # Bare ids — untouched by the qualified row.
            "gpt-5.5": 272_000,
            "gpt-4o": 128_000,
            "claude-opus-4-8": 1_000_000,
            "some-unknown-model": None,
        }
        for model_id, window in expected.items():
            with self.subTest(model=model_id):
                config = get_model_config(model_id)
                if window is None:
                    self.assertIsNone(config, f"{model_id} should not resolve")
                else:
                    self.assertEqual(config.context_window, window)

    def test_user_model_limits_override_still_reachable(self):
        """``_settings_limit`` is consulted only when get_model_config returns
        None, so every row added here shadows a user's explicit ``modelLimits``.

        The shadowed surface is the row's PREFIX, not just its key: the added
        row's derived base is ``openai/gpt-5.6``, so a ``modelLimits`` entry
        for any ``openai/gpt-5.6*`` id now loses to the table (they all get
        1,048,576, matching what their bare equivalents already get — so the
        qualified namespace mirrors the bare one rather than diverging).
        What must NOT happen is that claim spreading further, which is what
        this pins: an override outside the prefix still wins."""
        limits = {
            "openai/gpt-oss-120b": SimpleNamespace(
                context_window=131_072, max_output_tokens=None
            )
        }
        with mock.patch(
            "src.settings.settings.get_settings",
            return_value=SimpleNamespace(model_limits=limits, effort=""),
        ):
            self.assertEqual(
                get_context_window_for_model("openai/gpt-oss-120b"), 131_072
            )

    def test_pricing_resolves_through_the_vendor_prefix(self):
        """``get_pricing`` DOES strip the vendor prefix (its own documented
        tier 2), so one bare pricing key covers the OpenRouter id.

        RATES ARE OPENAI LIST, deliberately. These assertions previously read
        $0.10/$0.60, taken from a 2026-07-31 probe of OpenRouter — which was
        and still is running a 50% promotional discount off OpenAI's
        $0.20/$1.20. Pinning the promo made every OpenAI-direct run report
        HALF its real cost, and that is how it was found: an 89-task eval
        accounted $2.89 against a $7.20 invoice.

        One row serves both gateways and it holds LIST price. A discount is
        temporary and would rot into silent under-reporting the moment it
        ends; over-reporting an OpenRouter run is the safe direction for a
        benchmark whose cost figure gets compared against other agents'.
        """
        pricing = get_pricing(LUNA)
        self.assertIsNotNone(pricing, "luna must not be priced as unknown")
        self.assertAlmostEqual(pricing["input"] * 1_000_000, 0.20, places=6)
        self.assertAlmostEqual(pricing["output"] * 1_000_000, 1.20, places=6)

    def test_long_context_pricing_tier(self):
        """Above 272K prompt tokens the full request is repriced at 2x input
        and 1.5x output. A 1M-window model on a benchmark will cross that,
        and cost is a number the eval reports."""
        short = get_pricing(LUNA, input_tokens=100_000)
        long = get_pricing(LUNA, input_tokens=300_000)
        self.assertAlmostEqual(short["input"] * 1_000_000, 0.20, places=6)
        self.assertAlmostEqual(long["input"] * 1_000_000, 0.40, places=6)
        self.assertAlmostEqual(long["output"] * 1_000_000, 1.80, places=6)

    def test_cost_uses_the_long_tier_for_a_big_prompt(self):
        """End-to-end through compute_cost, which is what the eval reports."""
        cheap = compute_cost(LUNA, {"input_tokens": 100_000, "output_tokens": 1_000})
        pricey = compute_cost(LUNA, {"input_tokens": 300_000, "output_tokens": 1_000})
        self.assertAlmostEqual(cheap, 100_000 * 2e-7 + 1_000 * 1.2e-6, places=9)
        self.assertAlmostEqual(pricey, 300_000 * 4e-7 + 1_000 * 1.8e-6, places=9)


class TestEffortRoutingHasOneInjectionSite(unittest.TestCase):
    """``_EffortProvider`` used to inject ``reasoning_effort`` itself. With
    query.py doing it too, the wrapper's ``setdefault`` found the key already
    filled from ``settings.effort`` and the session's ``/effort`` was silently
    dropped — inverting the documented precedence. The wrapper is gone; this
    pins that it stays gone and that routing just forwards the level."""

    def test_effort_provider_class_is_removed(self):
        import src.server.agent_server as agent_server

        self.assertFalse(
            hasattr(agent_server, "_EffortProvider"),
            "a second injection site reintroduces the precedence inversion",
        )

    def test_routing_forwards_the_level_without_wrapping(self):
        from src.server.agent_server import _AgentSession

        session = object.__new__(_AgentSession)
        provider = _make_openrouter_mock()
        session.provider = provider
        session._effort = "max"
        turn_provider, thinking_effort = _AgentSession._turn_effort_routing(session)
        self.assertIs(turn_provider, provider, "provider must not be wrapped")
        self.assertEqual(thinking_effort, "max")

    def test_routing_passes_none_when_effort_unset(self):
        from src.server.agent_server import _AgentSession

        session = object.__new__(_AgentSession)
        session.provider = _make_openrouter_mock()
        session._effort = None
        self.assertIsNone(_AgentSession._turn_effort_routing(session)[1])

    def test_explicit_effort_beats_persisted_setting(self):
        """The precedence the double injection inverted, end to end."""
        with mock.patch(
            "src.settings.settings.get_settings",
            return_value=SimpleNamespace(effort="medium"),
        ):
            driver = TestOpenAICompatEffortOnTheWire("run")
            driver.setUp()
            try:
                kw = driver._drive_one_turn(
                    _make_openrouter_mock(), thinking_effort="max"
                )
            finally:
                driver.tearDown()
        self.assertEqual(
            (kw.get("extra_body") or {}).get("reasoning_effort"),
            "max",
            "session /effort must win over settings.effort",
        )


if __name__ == "__main__":
    unittest.main()

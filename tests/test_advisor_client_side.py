"""Client-side advisor path — helpers, dispatcher, end-to-end activation.

The Python-only client-side mode lets ``/advisor`` work on any provider
by routing advisor invocations through the tool dispatcher (a regular
``tool_use(name="advisor")`` block) instead of the Anthropic
server-side beta.

Test surface:
  * ``decide_advisor_mode`` — full activation truth table.
  * ``infer_provider_for_model`` — known + prefix + unknown shapes.
  * ``build_advisor_forwarded_messages`` — strips prior advisor blocks
    so they don't leak into the advisor's own forwarded context.
  * ``execute_client_advisor`` — wires through provider factory,
    returns text on success, error string on failure.
  * ``AdvisorTool._advisor_call`` — reads ctx.messages, forwards
    through execute, returns ToolResult.
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from src.tool_system.context import ToolContext
from src.tool_system.tools.advisor import AdvisorTool
from src.utils.advisor import (
    ADVISOR_MODE_CLIENT_SIDE,
    ADVISOR_MODE_INACTIVE,
    ADVISOR_MODE_SERVER_SIDE,
    build_advisor_forwarded_messages,
    build_client_advisor_tool_schema,
    decide_advisor_mode,
    execute_client_advisor,
    infer_provider_for_model,
)


class TestInferProviderForModel(unittest.TestCase):
    def test_exact_match_anthropic(self) -> None:
        self.assertEqual(infer_provider_for_model("claude-opus-4-6"), "anthropic")
        self.assertEqual(infer_provider_for_model("claude-sonnet-4-6"), "anthropic")

    def test_exact_match_openai(self) -> None:
        self.assertEqual(infer_provider_for_model("gpt-5.4"), "openai")

    def test_exact_match_glm(self) -> None:
        self.assertEqual(infer_provider_for_model("zai/glm-5"), "glm")

    def test_exact_match_gemini(self) -> None:
        self.assertEqual(infer_provider_for_model("gemini-2.5-pro"), "gemini")

    def test_prefix_fallback_anthropic(self) -> None:
        # claude-opus-4-7 isn't in PROVIDER_INFO yet but the prefix
        # rule routes it cleanly.
        self.assertEqual(
            infer_provider_for_model("claude-opus-4-7-future"), "anthropic"
        )

    def test_prefix_fallback_gemini(self) -> None:
        self.assertEqual(infer_provider_for_model("gemini-3.0-pro"), "gemini")

    def test_vendor_slash_routes_to_openrouter(self) -> None:
        # ``anthropic/claude-…`` is an OpenRouter route, not Anthropic
        # direct — the ``/`` shape after the more-specific prefix
        # checks routes to openrouter.
        self.assertEqual(
            infer_provider_for_model("anthropic/claude-sonnet-4.5"), "openrouter"
        )
        self.assertEqual(
            infer_provider_for_model("meta-llama/llama-3.3-70b-instruct"),
            "openrouter",
        )

    def test_glm_prefix_beats_openrouter_slash(self) -> None:
        # zai/ prefix is checked before the generic vendor/<model>
        # rule, so GLM stays with the GLM provider.
        self.assertEqual(infer_provider_for_model("zai/glm-4.7"), "glm")

    def test_unknown_model_returns_none(self) -> None:
        self.assertIsNone(infer_provider_for_model("totally-fake-zzz-9999"))
        self.assertIsNone(infer_provider_for_model(""))
        self.assertIsNone(infer_provider_for_model(None))  # type: ignore[arg-type]


class TestDecideAdvisorMode(unittest.TestCase):
    """Mode-decision truth table."""

    def setUp(self) -> None:
        os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)

    def _first_party_provider(self) -> MagicMock:
        provider = MagicMock()
        provider.has_custom_endpoint = MagicMock(return_value=False)
        return provider

    def _third_party_provider(self) -> MagicMock:
        provider = MagicMock()
        provider.has_custom_endpoint = MagicMock(return_value=True)
        return provider

    def test_inactive_when_no_advisor_model(self) -> None:
        mode = decide_advisor_mode(
            self._first_party_provider(), "claude-opus-4-6", ""
        )
        self.assertEqual(mode, ADVISOR_MODE_INACTIVE)

    def test_inactive_when_env_disabled(self) -> None:
        with patch.dict(
            os.environ, {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}, clear=False
        ):
            mode = decide_advisor_mode(
                self._first_party_provider(),
                "claude-opus-4-6",
                "claude-opus-4-6",
            )
            self.assertEqual(mode, ADVISOR_MODE_INACTIVE)

    def test_server_side_for_1p_with_valid_models(self) -> None:
        with patch(
            "src.state.cache_state.is_first_party_provider",
            return_value=True,
        ):
            mode = decide_advisor_mode(
                self._first_party_provider(),
                "claude-opus-4-6",
                "claude-opus-4-6",
            )
            self.assertEqual(mode, ADVISOR_MODE_SERVER_SIDE)

    def test_client_side_when_1p_with_force_client(self) -> None:
        with patch(
            "src.state.cache_state.is_first_party_provider",
            return_value=True,
        ):
            mode = decide_advisor_mode(
                self._first_party_provider(),
                "claude-opus-4-6",
                "claude-opus-4-6",
                force_client_mode=True,
            )
            self.assertEqual(mode, ADVISOR_MODE_CLIENT_SIDE)

    def test_client_side_for_1p_with_unsupported_base_model(self) -> None:
        # opus-4-5 doesn't support server-side advisor → falls back
        # to client-side as long as the advisor model routes.
        with patch(
            "src.state.cache_state.is_first_party_provider",
            return_value=True,
        ):
            mode = decide_advisor_mode(
                self._first_party_provider(),
                "claude-opus-4-5",
                "claude-opus-4-6",
            )
            self.assertEqual(mode, ADVISOR_MODE_CLIENT_SIDE)

    def test_client_side_for_3p_provider(self) -> None:
        # 3P never qualifies for server-side; client-side is the only
        # path. The main loop's model doesn't matter here.
        with patch(
            "src.state.cache_state.is_first_party_provider",
            return_value=False,
        ):
            mode = decide_advisor_mode(
                self._third_party_provider(),
                "gpt-5.4",
                "claude-opus-4-6",
            )
            self.assertEqual(mode, ADVISOR_MODE_CLIENT_SIDE)

    def test_client_side_cross_provider(self) -> None:
        # 1P main loop with a Gemini advisor — server-side rejects
        # the Gemini model, falls through to client-side which routes
        # gemini-2.5-pro to the gemini provider.
        with patch(
            "src.state.cache_state.is_first_party_provider",
            return_value=True,
        ):
            mode = decide_advisor_mode(
                self._first_party_provider(),
                "claude-opus-4-6",
                "gemini-2.5-pro",
            )
            self.assertEqual(mode, ADVISOR_MODE_CLIENT_SIDE)

    def test_inactive_when_force_client_but_unrouted_model(self) -> None:
        # Even with force_client_mode, an unroutable advisor model
        # returns INACTIVE — there's no provider to send the request to.
        mode = decide_advisor_mode(
            self._first_party_provider(),
            "claude-opus-4-6",
            "totally-fake-xyz-9999",
            force_client_mode=True,
        )
        self.assertEqual(mode, ADVISOR_MODE_INACTIVE)

    def test_inactive_when_no_provider_and_no_force(self) -> None:
        # Provider=None + no force_client + advisor_model set: the
        # server-side gate fails (needs is_advisor_enabled which needs
        # a provider), and client-side activates as long as advisor
        # routes. This matches the "/advisor pre-startup" surface.
        mode = decide_advisor_mode(None, "claude-opus-4-6", "claude-opus-4-6")
        # No provider → can't check first-party → falls to client-side
        # (advisor model routes to anthropic).
        self.assertEqual(mode, ADVISOR_MODE_CLIENT_SIDE)


class TestBuildAdvisorForwardedMessages(unittest.TestCase):
    """Forwarded messages must strip prior advisor blocks so the
    advisor doesn't see its own previous consultations as input."""

    def test_strips_server_side_advisor_blocks(self) -> None:
        from src.types.messages import AssistantMessage, UserMessage
        msgs = [
            UserMessage(content="hi"),
            AssistantMessage(
                content=[
                    {"type": "text", "text": "thinking"},
                    {
                        "type": "server_tool_use",
                        "id": "srv_1",
                        "name": "advisor",
                        "input": {},
                    },
                    {
                        "type": "advisor_tool_result",
                        "tool_use_id": "srv_1",
                        "content": {"type": "advisor_result", "text": "old advice"},
                    },
                    {"type": "text", "text": "after"},
                ],
            ),
        ]
        out = build_advisor_forwarded_messages(msgs)
        for m in out:
            if m.get("role") != "assistant":
                continue
            c = m.get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict):
                        self.assertNotEqual(b.get("type"), "server_tool_use")
                        self.assertNotEqual(b.get("type"), "advisor_tool_result")

    def test_strips_worker_self_advisor_tool_use_marker(self) -> None:
        """The worker's own ``tool_use(name=advisor)`` is what triggered
        the advisor call. Including a `[Tool call: advisor({})]` marker
        in the flattened text invites the advisor to LARP as the
        worker. Strip it so the advisor sees the conversation as if
        it's being asked to opine, not to ack a tool invocation."""
        from src.types.messages import AssistantMessage, UserMessage
        msgs = [
            UserMessage(content="task"),
            AssistantMessage(content=[
                {"type": "text", "text": "thinking about it"},
                {"type": "tool_use", "id": "t1", "name": "advisor", "input": {}},
            ]),
        ]
        out = build_advisor_forwarded_messages(msgs)
        for m in out:
            self.assertNotIn("[Tool call: advisor", m["content"])

    def test_strips_orphan_pairing_cruft_user_message(self) -> None:
        """The orphan-pairing pass injects a synthetic
        ``[Tool result missing due to internal error]`` user message
        to keep tool_use/tool_result pairing valid for the API. That
        cruft must be filtered from the advisor's view — otherwise
        the advisor thinks the worker just hit a tool failure and
        responds to that instead of the actual task."""
        from src.types.messages import AssistantMessage, UserMessage
        msgs = [
            UserMessage(content="real task"),
            AssistantMessage(content=[
                {"type": "text", "text": "my plan is X"},
                {"type": "tool_use", "id": "t1", "name": "advisor", "input": {}},
            ]),
        ]
        out = build_advisor_forwarded_messages(msgs)
        joined = "\n".join(m["content"] for m in out)
        self.assertNotIn("[Tool result missing due to internal error]", joined)
        self.assertNotIn("[Tool use interrupted]", joined)

    def test_returns_plain_dicts_safe_to_send(self) -> None:
        from src.types.messages import UserMessage
        out = build_advisor_forwarded_messages([UserMessage(content="hello")])
        self.assertTrue(all(isinstance(m, dict) for m in out))


class TestExecuteClientAdvisor(unittest.TestCase):
    """``execute_client_advisor`` integration — provider factory wiring.

    The advisor uses ``chat_stream_response`` (cross-provider abort_signal
    support) with a fallback to plain ``chat`` for providers that don't
    implement it.
    """

    def _make_anthropic_shaped_provider(self, content: str = "advice") -> MagicMock:
        """Mock that passes the isinstance check for AnthropicProvider."""
        from src.providers.anthropic_provider import AnthropicProvider
        provider = MagicMock(spec=AnthropicProvider)
        provider.chat_stream_response = MagicMock(
            return_value=MagicMock(content=content)
        )
        return provider

    def _make_openai_shape_provider(self, content: str = "advice") -> MagicMock:
        """Mock that does NOT pass isinstance for AnthropicProvider —
        the function should detect it as OpenAI-shape and prepend a
        system-role message instead of passing system=kwarg."""
        provider = MagicMock()  # bare MagicMock, no spec
        provider.chat_stream_response = MagicMock(
            return_value=MagicMock(content=content)
        )
        return provider

    def test_returns_text_on_success_anthropic(self) -> None:
        fake_provider = self._make_anthropic_shaped_provider("here is advice")
        with patch(
            "src.providers.get_provider_class", return_value=lambda **kw: fake_provider
        ):
            with patch(
                "src.config.get_provider_config", return_value={"api_key": "test"}
            ):
                ok, text = execute_client_advisor(
                    "claude-opus-4-6", [{"role": "user", "content": "hi"}]
                )
        self.assertTrue(ok)
        self.assertEqual(text, "here is advice")
        # Anthropic-shaped → system goes as kwarg, NOT prepended to messages.
        call = fake_provider.chat_stream_response.call_args
        self.assertEqual(call.kwargs.get("tools"), [])
        self.assertIn("system", call.kwargs)
        self.assertIn("reviewer", call.kwargs["system"].lower())
        # Messages array unchanged (no system message prepended).
        forwarded_messages = call.args[0]
        self.assertEqual(forwarded_messages[0].get("role"), "user")

    def test_openai_shape_gets_system_as_first_message(self) -> None:
        fake_provider = self._make_openai_shape_provider("advice from openai")
        with patch(
            "src.providers.get_provider_class", return_value=lambda **kw: fake_provider
        ):
            with patch(
                "src.config.get_provider_config", return_value={"api_key": "test"}
            ):
                ok, text = execute_client_advisor(
                    "gpt-5.4", [{"role": "user", "content": "hi"}]
                )
        self.assertTrue(ok)
        self.assertEqual(text, "advice from openai")
        # OpenAI-shape → system prepended as first message, NO system kwarg.
        call = fake_provider.chat_stream_response.call_args
        self.assertNotIn("system", call.kwargs)
        forwarded_messages = call.args[0]
        self.assertEqual(forwarded_messages[0]["role"], "system")
        self.assertIn("reviewer", forwarded_messages[0]["content"].lower())
        self.assertEqual(forwarded_messages[1]["role"], "user")

    def test_returns_error_when_model_unroutable(self) -> None:
        ok, text = execute_client_advisor(
            "totally-fake-zzz-9999", [{"role": "user", "content": "hi"}]
        )
        self.assertFalse(ok)
        self.assertIn("cannot route", text.lower())

    def test_returns_error_when_provider_raises(self) -> None:
        fake_provider = self._make_anthropic_shaped_provider()
        fake_provider.chat_stream_response = MagicMock(
            side_effect=RuntimeError("network down")
        )
        with patch(
            "src.providers.get_provider_class", return_value=lambda **kw: fake_provider
        ):
            with patch(
                "src.config.get_provider_config", return_value={"api_key": "test"}
            ):
                ok, text = execute_client_advisor(
                    "claude-opus-4-6", [{"role": "user", "content": "hi"}]
                )
        self.assertFalse(ok)
        self.assertIn("network down", text)

    def test_returns_error_when_response_empty(self) -> None:
        fake_provider = self._make_anthropic_shaped_provider("")
        with patch(
            "src.providers.get_provider_class", return_value=lambda **kw: fake_provider
        ):
            with patch(
                "src.config.get_provider_config", return_value={"api_key": "test"}
            ):
                ok, text = execute_client_advisor(
                    "claude-opus-4-6", [{"role": "user", "content": "hi"}]
                )
        self.assertFalse(ok)
        self.assertIn("no text", text.lower())

    def test_routes_through_main_provider_when_proxy(self) -> None:
        # User's main provider is OpenAI pointed at litellm (custom
        # base_url). The advisor model is claude-opus-4-7 which would
        # normally infer to Anthropic — but the proxy assumption says
        # "use the same proxy for both". We expect the advisor to be
        # built via OpenAIProvider, NOT AnthropicProvider.
        from src.providers.openai_provider import OpenAIProvider
        # Make the main provider look like an OpenAI proxy: base_url
        # set to something other than the default openai endpoint.
        main_provider = MagicMock(spec=OpenAIProvider)
        main_provider.base_url = "https://litellm.singula.ai"
        main_provider.__class__ = OpenAIProvider

        # Spy on the constructor — the advisor provider should be
        # built from OpenAIProvider, not from infer_provider_for_model's
        # anthropic result.
        constructed = {}

        def _fake_openai_init(**kwargs: Any) -> Any:
            constructed["cls"] = "OpenAIProvider"
            constructed["kwargs"] = kwargs
            inst = MagicMock(spec=OpenAIProvider)
            inst.chat_stream_response = MagicMock(
                return_value=MagicMock(content="proxied advice")
            )
            return inst

        with patch(
            "src.config.get_provider_config",
            return_value={"api_key": "k", "base_url": "https://litellm.singula.ai"},
        ):
            with patch.object(OpenAIProvider, "__new__", lambda cls, **kw: _fake_openai_init(**kw)):
                ok, text = execute_client_advisor(
                    "claude-opus-4-7",
                    [{"role": "user", "content": "hi"}],
                    main_provider=main_provider,
                )
        self.assertTrue(ok)
        self.assertEqual(text, "proxied advice")
        self.assertEqual(constructed["cls"], "OpenAIProvider")
        # Model swapped to the advisor's choice, base_url preserved
        # (came from get_provider_config).
        self.assertEqual(constructed["kwargs"]["model"], "claude-opus-4-7")
        self.assertEqual(
            constructed["kwargs"]["base_url"], "https://litellm.singula.ai"
        )

    def test_uses_inferred_provider_when_main_is_not_proxy(self) -> None:
        # 1P Anthropic main loop (default base_url). Advisor model is
        # gemini-2.5-pro → should route via inference to Gemini, NOT
        # reuse the Anthropic main provider.
        from src.providers.anthropic_provider import AnthropicProvider
        main_provider = MagicMock(spec=AnthropicProvider)
        main_provider.base_url = "https://api.anthropic.com"  # default

        fake_gemini = MagicMock()
        fake_gemini.chat_stream_response = MagicMock(
            return_value=MagicMock(content="gemini says hi")
        )
        with patch(
            "src.providers.get_provider_class",
            return_value=lambda **kw: fake_gemini,
        ):
            with patch(
                "src.config.get_provider_config", return_value={"api_key": "k"}
            ):
                ok, text = execute_client_advisor(
                    "gemini-2.5-pro",
                    [{"role": "user", "content": "hi"}],
                    main_provider=main_provider,
                )
        self.assertTrue(ok)
        self.assertEqual(text, "gemini says hi")

    def test_falls_back_to_chat_when_stream_unimplemented(self) -> None:
        # Older / stub providers may not implement chat_stream_response.
        # The function should fall back to plain chat() gracefully.
        from src.providers.anthropic_provider import AnthropicProvider
        fake_provider = MagicMock(spec=AnthropicProvider)
        fake_provider.chat_stream_response = MagicMock(
            side_effect=NotImplementedError("no streaming"),
        )
        fake_provider.chat = MagicMock(return_value=MagicMock(content="fallback worked"))
        with patch(
            "src.providers.get_provider_class", return_value=lambda **kw: fake_provider
        ):
            with patch(
                "src.config.get_provider_config", return_value={"api_key": "test"}
            ):
                ok, text = execute_client_advisor(
                    "claude-opus-4-6", [{"role": "user", "content": "hi"}]
                )
        self.assertTrue(ok)
        self.assertEqual(text, "fallback worked")
        # The fallback path did NOT pass abort_signal (sync chat doesn't
        # consistently accept it across providers).
        fallback_call = fake_provider.chat.call_args
        self.assertNotIn("abort_signal", fallback_call.kwargs)


class TestAdvisorTool(unittest.TestCase):
    """The registered AdvisorTool — wires ctx.messages → execute."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self._tmpdir = Path(tempfile.mkdtemp(prefix="advisor_tool_test_"))

    def test_is_hidden_from_default_pool(self) -> None:
        # is_enabled=False keeps it out of /tools and the default schema
        # construction. _call_model_sync injects the schema manually
        # when client-side mode is active.
        self.assertFalse(AdvisorTool.is_enabled())

    def test_call_returns_advice_text(self) -> None:
        ctx = ToolContext(workspace_root=self._tmpdir)
        ctx.messages = [{"role": "user", "content": "task"}]
        from src.settings.settings import get_settings
        # Patch settings to provide an advisor_model so the tool doesn't
        # short-circuit on the "no model configured" branch.
        fake_settings = MagicMock()
        fake_settings.advisor_model = "claude-opus-4-6"
        with patch("src.settings.settings.get_settings", return_value=fake_settings):
            with patch(
                "src.utils.advisor.execute_client_advisor",
                return_value=(True, "use the foo pattern"),
            ):
                result = AdvisorTool.call({}, ctx)
        self.assertEqual(result.name, "advisor")
        self.assertEqual(result.output, "use the foo pattern")
        self.assertFalse(result.is_error)

    def test_call_marks_error_when_execute_fails(self) -> None:
        ctx = ToolContext(workspace_root=self._tmpdir)
        ctx.messages = [{"role": "user", "content": "task"}]
        fake_settings = MagicMock()
        fake_settings.advisor_model = "claude-opus-4-6"
        with patch("src.settings.settings.get_settings", return_value=fake_settings):
            with patch(
                "src.utils.advisor.execute_client_advisor",
                return_value=(False, "Advisor unavailable: foo"),
            ):
                result = AdvisorTool.call({}, ctx)
        self.assertTrue(result.is_error)
        self.assertIn("unavailable", result.output)

    def test_call_when_no_model_configured_returns_error(self) -> None:
        ctx = ToolContext(workspace_root=self._tmpdir)
        fake_settings = MagicMock()
        fake_settings.advisor_model = ""
        with patch("src.settings.settings.get_settings", return_value=fake_settings):
            result = AdvisorTool.call({}, ctx)
        self.assertTrue(result.is_error)
        self.assertIn("no advisor_model", result.output.lower())


class TestClientAdvisorToolSchema(unittest.TestCase):
    """The schema sent to the API in client-side mode is regular
    tool_use shape — no ``type`` discriminator (that's the server-side
    ``advisor_20260301`` field)."""

    def test_schema_is_regular_tool_use_shape(self) -> None:
        schema = build_client_advisor_tool_schema()
        self.assertEqual(schema["name"], "advisor")
        self.assertIn("description", schema)
        self.assertIn("input_schema", schema)
        # Crucially absent — the type discriminator would crash 3P endpoints.
        self.assertNotIn("type", schema)

    def test_schema_input_takes_no_params(self) -> None:
        schema = build_client_advisor_tool_schema()
        props = schema["input_schema"].get("properties")
        self.assertEqual(props, {})
        self.assertEqual(
            schema["input_schema"].get("additionalProperties"), False
        )


if __name__ == "__main__":
    unittest.main()

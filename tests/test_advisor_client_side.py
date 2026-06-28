"""Client-side advisor path — helpers, dispatcher, end-to-end activation.

The Python-only client-side mode lets ``/advisor`` work on any provider
by routing advisor invocations through the tool dispatcher (a regular
``tool_use(name="advisor")`` block) instead of the Anthropic
server-side beta.

Test surface:
  * ``decide_advisor_mode`` — full activation truth table.
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
)


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
        # Server-side now requires advisor_provider == "anthropic" too.
        with patch(
            "src.state.cache_state.is_first_party_provider",
            return_value=True,
        ):
            mode = decide_advisor_mode(
                self._first_party_provider(),
                "claude-opus-4-6",
                "claude-opus-4-6",
                advisor_provider="anthropic",
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
                advisor_provider="anthropic",
            )
            self.assertEqual(mode, ADVISOR_MODE_CLIENT_SIDE)

    def test_client_side_for_1p_with_unsupported_base_model(self) -> None:
        # opus-4-5 doesn't support server-side advisor → falls back
        # to client-side as long as the advisor provider is configured.
        with patch(
            "src.state.cache_state.is_first_party_provider",
            return_value=True,
        ):
            mode = decide_advisor_mode(
                self._first_party_provider(),
                "claude-opus-4-5",
                "claude-opus-4-6",
                advisor_provider="anthropic",
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
                advisor_provider="anthropic",
            )
            self.assertEqual(mode, ADVISOR_MODE_CLIENT_SIDE)

    def test_client_side_cross_provider(self) -> None:
        # 1P main loop with a Gemini advisor — server-side rejects
        # because advisor_provider != "anthropic"; falls through to
        # client-side with the gemini provider.
        with patch(
            "src.state.cache_state.is_first_party_provider",
            return_value=True,
        ):
            mode = decide_advisor_mode(
                self._first_party_provider(),
                "claude-opus-4-6",
                "gemini-2.5-pro",
                advisor_provider="gemini",
            )
            self.assertEqual(mode, ADVISOR_MODE_CLIENT_SIDE)

    def test_inactive_when_force_client_but_unknown_provider(self) -> None:
        # Even with force_client_mode, an unknown provider key
        # returns INACTIVE — no Provider class to instantiate.
        mode = decide_advisor_mode(
            self._first_party_provider(),
            "claude-opus-4-6",
            "totally-fake-xyz-9999",
            force_client_mode=True,
            advisor_provider="not-a-real-provider-zzz",
        )
        self.assertEqual(mode, ADVISOR_MODE_INACTIVE)

    def test_inactive_when_advisor_provider_missing(self) -> None:
        # Multi-provider rewrite: advisor_provider is now REQUIRED.
        # Even with a valid advisor_model and a first-party main
        # provider, missing advisor_provider → INACTIVE. This guards
        # against the pre-rewrite hardcoded ``claude-`` → anthropic
        # inference silently routing to the wrong endpoint.
        mode = decide_advisor_mode(
            None, "claude-opus-4-6", "claude-opus-4-6"
        )
        self.assertEqual(mode, ADVISOR_MODE_INACTIVE)


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

    def test_format_advisor_status_compact_label(self) -> None:
        """``format_advisor_status`` returns a single short segment with
        the canonical model (claude- prefix stripped) + the mode label.
        Used wherever a status surface renders the advisor segment."""
        from src.utils.advisor import format_advisor_status
        from unittest.mock import patch
        # Mock settings to return a configured advisor model.
        with patch("src.utils.advisor._env_truthy", return_value=False), \
             patch("src.settings.settings.get_settings") as get_s, \
             patch("src.models.model.canonical_model_name", side_effect=lambda x: x):
            fake = type("S", (), {"advisor_model": "claude-opus-4-7", "advisor_provider": "anthropic", "advisor_client_mode": False, "advisor_enabled": True})()
            get_s.return_value = fake
            out = format_advisor_status(None, "claude-haiku-4-5")
        self.assertIsNotNone(out)
        # claude- prefix stripped for brevity
        self.assertIn("opus-4-7", out)
        self.assertNotIn("claude-opus", out)
        # Colon-separated qualifier (matches /advisor input syntax).
        self.assertIn("anthropic:opus-4-7", out)
        # Mode label is one of the three known values
        self.assertTrue(
            "(server)" in out or "(client)" in out or "(inactive)" in out,
            f"unexpected mode label in: {out!r}",
        )

    def test_format_advisor_status_returns_none_when_unset(self) -> None:
        from src.utils.advisor import format_advisor_status
        from unittest.mock import patch
        with patch("src.utils.advisor._env_truthy", return_value=False), \
             patch("src.settings.settings.get_settings") as get_s:
            fake = type("S", (), {"advisor_model": "", "advisor_provider": "", "advisor_client_mode": False, "advisor_enabled": True})()
            get_s.return_value = fake
            out = format_advisor_status(None, "claude-haiku-4-5")
        self.assertIsNone(out)

    def test_format_advisor_status_returns_none_when_env_disabled(self) -> None:
        from src.utils.advisor import format_advisor_status
        from unittest.mock import patch
        import os
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}, clear=False), \
             patch("src.settings.settings.get_settings") as get_s, \
             patch("src.models.model.canonical_model_name", side_effect=lambda x: x):
            fake = type("S", (), {"advisor_model": "claude-opus-4-7", "advisor_provider": "anthropic", "advisor_client_mode": False, "advisor_enabled": True})()
            get_s.return_value = fake
            out = format_advisor_status(None, "claude-haiku-4-5")
        # Even with model set, env-disable → mode is INACTIVE → label shows
        self.assertIsNotNone(out)
        self.assertIn("(inactive)", out)

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
                ok, text, _usage = execute_client_advisor(
                    "claude-opus-4-6", [{"role": "user", "content": "hi"}],
                    advisor_provider="anthropic",
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
                ok, text, _usage = execute_client_advisor(
                    "gpt-5.4", [{"role": "user", "content": "hi"}],
                    advisor_provider="openai",
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

    def test_returns_error_when_provider_missing(self) -> None:
        # Multi-provider rewrite: empty advisor_provider → fail-fast.
        ok, text, _usage = execute_client_advisor(
            "claude-opus-4-7", [{"role": "user", "content": "hi"}]
        )
        self.assertFalse(ok)
        self.assertIn("advisor_provider", text.lower())

    def test_returns_error_when_provider_unknown(self) -> None:
        # An unknown provider key (no Provider class registered) → fail-fast.
        ok, text, _usage = execute_client_advisor(
            "claude-opus-4-7", [{"role": "user", "content": "hi"}],
            advisor_provider="totally-fake-zzz-9999",
        )
        self.assertFalse(ok)
        self.assertIn("provider", text.lower())

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
                ok, text, _usage = execute_client_advisor(
                    "claude-opus-4-6", [{"role": "user", "content": "hi"}],
                    advisor_provider="anthropic",
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
                ok, text, _usage = execute_client_advisor(
                    "claude-opus-4-6", [{"role": "user", "content": "hi"}],
                    advisor_provider="anthropic",
                )
        self.assertFalse(ok)
        self.assertIn("no text", text.lower())

    def test_uses_explicit_provider_for_routing(self) -> None:
        # Multi-provider rewrite: ``advisor_provider`` decides routing,
        # NOT the model name. Even if the model name looks like an
        # Anthropic model, passing ``advisor_provider="openai"`` should
        # build via the openai provider config (e.g. litellm).
        from src.providers.openai_provider import OpenAIProvider
        constructed = {}

        def _fake_openai_init(**kwargs: Any) -> Any:
            constructed["cls"] = "OpenAIProvider"
            constructed["kwargs"] = kwargs
            inst = MagicMock(spec=OpenAIProvider)
            inst.chat_stream_response = MagicMock(
                return_value=MagicMock(content="via openai litellm"),
            )
            return inst

        with patch(
            "src.providers.get_provider_class",
            return_value=lambda **kw: _fake_openai_init(**kw),
        ):
            with patch(
                "src.config.get_provider_config",
                return_value={
                    "api_key": "k",
                    "base_url": "https://litellm.singula.ai",
                },
            ):
                ok, text, _usage = execute_client_advisor(
                    "claude-opus-4-7",
                    [{"role": "user", "content": "hi"}],
                    advisor_provider="openai",
                )
        self.assertTrue(ok)
        self.assertEqual(text, "via openai litellm")
        self.assertEqual(constructed["kwargs"]["model"], "claude-opus-4-7")
        self.assertEqual(
            constructed["kwargs"]["base_url"], "https://litellm.singula.ai",
        )

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
                ok, text, _usage = execute_client_advisor(
                    "claude-opus-4-6", [{"role": "user", "content": "hi"}],
                    advisor_provider="anthropic",
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
        fake_settings.advisor_provider = "anthropic"
        with patch("src.settings.settings.get_settings", return_value=fake_settings):
            with patch(
                "src.utils.advisor.execute_client_advisor",
                return_value=(True, "use the foo pattern", {"input_tokens": 0, "output_tokens": 0}),
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
        fake_settings.advisor_provider = "anthropic"
        with patch("src.settings.settings.get_settings", return_value=fake_settings):
            with patch(
                "src.utils.advisor.execute_client_advisor",
                return_value=(False, "Advisor unavailable: foo", {"input_tokens": 0, "output_tokens": 0}),
            ):
                result = AdvisorTool.call({}, ctx)
        self.assertTrue(result.is_error)
        self.assertIn("unavailable", result.output)

    def test_call_accumulates_advisor_tokens_onto_ctx(self) -> None:
        """The dispatcher writes ``advisor_input_tokens`` /
        ``advisor_output_tokens`` onto the ToolContext on every
        consultation so the REPL/TUI status bar can surface them next
        to the worker's totals. Multiple calls accumulate (not replace)."""
        ctx = ToolContext(workspace_root=self._tmpdir)
        ctx.messages = [{"role": "user", "content": "task"}]
        fake_settings = MagicMock()
        fake_settings.advisor_model = "claude-opus-4-6"
        fake_settings.advisor_provider = "anthropic"
        with patch("src.settings.settings.get_settings", return_value=fake_settings):
            with patch(
                "src.utils.advisor.execute_client_advisor",
                return_value=(True, "advice 1", {"input_tokens": 100, "output_tokens": 50}),
            ):
                AdvisorTool.call({}, ctx)
            self.assertEqual(ctx.advisor_input_tokens, 100)
            self.assertEqual(ctx.advisor_output_tokens, 50)
            # Second call ACCUMULATES on top of the first.
            with patch(
                "src.utils.advisor.execute_client_advisor",
                return_value=(True, "advice 2", {"input_tokens": 30, "output_tokens": 10}),
            ):
                AdvisorTool.call({}, ctx)
            self.assertEqual(ctx.advisor_input_tokens, 130)
            self.assertEqual(ctx.advisor_output_tokens, 60)

    def test_call_when_no_model_configured_returns_error(self) -> None:
        ctx = ToolContext(workspace_root=self._tmpdir)
        fake_settings = MagicMock()
        fake_settings.advisor_model = ""
        fake_settings.advisor_provider = ""
        with patch("src.settings.settings.get_settings", return_value=fake_settings):
            result = AdvisorTool.call({}, ctx)
        self.assertTrue(result.is_error)
        self.assertIn("advisor_provider", result.output.lower())


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

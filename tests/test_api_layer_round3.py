"""ch04 round-3 acceptance tests: max-tokens wiring (G0), cost heads (G1),
x-client-request-id (G2), the 529 retry lane (G3), and client laziness (G4).

Gap analysis: my-docs/ch04-api-layer-round3-gap-analysis.md rev 2.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from src.bootstrap.state import (
    get_model_usage,
    get_total_cost_usd,
    reset_state_for_tests,
)
from src.cost_tracker import record_api_usage
from src.models.context import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    resolve_max_output_tokens,
)
from src.providers.base import ChatResponse
from src.query.query import (
    FOREGROUND_529_RETRY_SOURCES,
    MAX_529_RETRIES,
    QueryParams,
    _is_overloaded_error,
    _retry_after_seconds,
    run_query,
)
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import SystemMessage, UserMessage
from src.utils.abort_controller import AbortController


@pytest.fixture(autouse=True)
def _reset_cost_state():
    reset_state_for_tests()
    yield
    reset_state_for_tests()


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# G0 — resolve_max_output_tokens
# ---------------------------------------------------------------------------


class TestResolveMaxOutputTokens(unittest.TestCase):
    def test_override_wins(self):
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "9"}):
            self.assertEqual(resolve_max_output_tokens(64_000, "claude-x"), 64_000)

    def test_env_override(self):
        with mock.patch.dict(
            "os.environ", {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "12345"}
        ):
            self.assertEqual(resolve_max_output_tokens(None, "claude-x"), 12345)

    def test_invalid_env_ignored(self):
        for bad in ("abc", "-5", "0", ""):
            with self.subTest(bad=bad), mock.patch.dict(
                "os.environ", {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": bad}
            ):
                result = resolve_max_output_tokens(None, "unknown-model-xyz")
                self.assertEqual(result, DEFAULT_MAX_OUTPUT_TOKENS)

    def test_per_model_table_then_default(self):
        import os

        os.environ.pop("CLAUDE_CODE_MAX_OUTPUT_TOKENS", None)
        self.assertEqual(
            resolve_max_output_tokens(None, "unknown-model-xyz"),
            DEFAULT_MAX_OUTPUT_TOKENS,
        )
        self.assertEqual(
            resolve_max_output_tokens(None, None), DEFAULT_MAX_OUTPUT_TOKENS
        )


# ---------------------------------------------------------------------------
# G1 — record_api_usage + the loop head
# ---------------------------------------------------------------------------


class TestRecordApiUsage(unittest.TestCase):
    def setUp(self):
        reset_state_for_tests()

    tearDown = setUp

    def test_records_per_model_and_total(self):
        record_api_usage(
            "claude-test", {"input_tokens": 100, "output_tokens": 50}
        )
        record_api_usage(
            "claude-test", {"input_tokens": 10, "output_tokens": 5}
        )
        usage = get_model_usage()["claude-test"]
        self.assertEqual(usage.input_tokens, 110)
        self.assertEqual(usage.output_tokens, 55)

    def test_tolerates_empty_and_none(self):
        record_api_usage("claude-test", {})
        record_api_usage("claude-test", None)
        usage = get_model_usage()["claude-test"]
        self.assertEqual(usage.input_tokens, 0)


def _make_params(workspace: Path, provider, **kw) -> QueryParams:
    registry = build_default_registry()
    return QueryParams(
        messages=[UserMessage(content="Hi")],
        system_prompt="You are helpful.",
        tools=registry.list_tools(),
        tool_registry=registry,
        tool_use_context=ToolContext(workspace_root=workspace),
        provider=provider,
        abort_controller=AbortController(),
        max_turns=4,
        **kw,
    )


def _completion(content="Done.") -> ChatResponse:
    return ChatResponse(
        content=content,
        model="claude-test",
        usage={"input_tokens": 7, "output_tokens": 3},
        finish_reason="end_turn",
        tool_uses=None,
    )


class TestLoopCostHead(unittest.TestCase):
    def setUp(self):
        reset_state_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        reset_state_for_tests()

    def test_main_loop_records_usage(self):
        provider = mock.MagicMock()
        provider.model = "claude-test"
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion()

        _messages, terminal = _run(run_query(_make_params(self.workspace, provider)))
        self.assertEqual(terminal.reason, "completed")
        usage = get_model_usage().get("claude-test")
        self.assertIsNotNone(usage)
        self.assertEqual(usage.input_tokens, 7)
        self.assertEqual(usage.output_tokens, 3)


# ---------------------------------------------------------------------------
# G2 — first-party detection + request-id
# ---------------------------------------------------------------------------


class TestFirstPartyRequestId(unittest.TestCase):
    def _provider(self, base_url=None):
        from src.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key="k", base_url=base_url)

    def test_default_is_first_party(self):
        import os

        os.environ.pop("ANTHROPIC_BASE_URL", None)
        p = self._provider()
        self.assertTrue(p._is_first_party())
        headers = p._request_id_headers()
        self.assertIn("x-client-request-id", headers)
        # UUID differs per request.
        self.assertNotEqual(
            p._request_id_headers()["x-client-request-id"],
            p._request_id_headers()["x-client-request-id"],
        )

    def test_constructor_base_url_custom_is_not_first_party(self):
        p = self._provider(base_url="https://proxy.example.com/v1")
        self.assertFalse(p._is_first_party())
        self.assertEqual(p._request_id_headers(), {})
        self.assertTrue(p.has_custom_endpoint())

    def test_env_base_url_is_not_first_party(self):
        # The SDK falls back to ANTHROPIC_BASE_URL when constructor
        # base_url is None — env proxies must NOT receive the header
        # (critic-corrected check).
        with mock.patch.dict(
            "os.environ", {"ANTHROPIC_BASE_URL": "https://proxy.corp/v1"}
        ):
            p = self._provider()
            self.assertFalse(p._is_first_party())
            self.assertEqual(p._request_id_headers(), {})
            self.assertTrue(p.has_custom_endpoint())

    def test_explicit_first_party_host_allowed(self):
        p = self._provider(base_url="https://api.anthropic.com")
        self.assertTrue(p._is_first_party())


# ---------------------------------------------------------------------------
# G3 — overloaded retry lane
# ---------------------------------------------------------------------------


class _Overloaded(Exception):
    status_code = 529


class TestOverloadedClassifier(unittest.TestCase):
    def test_status_code(self):
        self.assertTrue(_is_overloaded_error(_Overloaded("boom")))

    def test_text_match(self):
        self.assertTrue(_is_overloaded_error(Exception("overloaded_error: x")))

    def test_other_errors_false(self):
        self.assertFalse(_is_overloaded_error(Exception("connection reset")))

    def test_retry_after_header(self):
        e = _Overloaded("x")
        e.response = mock.MagicMock()
        e.response.headers = {"retry-after": "2"}
        self.assertEqual(_retry_after_seconds(e, 0.5), 2.0)
        e.response.headers = {}
        self.assertEqual(_retry_after_seconds(e, 0.5), 0.5)


class TestRetryLane(unittest.TestCase):
    def setUp(self):
        reset_state_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        reset_state_for_tests()

    def _flaky_provider(self, failures: int):
        provider = mock.MagicMock()
        provider.model = "claude-test"
        provider.chat_stream_response.side_effect = NotImplementedError()
        calls = {"n": 0}

        def chat(*a, **k):
            calls["n"] += 1
            if calls["n"] <= failures:
                raise _Overloaded("Error code: 529 overloaded_error")
            return _completion()

        provider.chat.side_effect = chat
        provider._calls = calls
        return provider

    def test_succeeds_on_attempt_two_with_one_warning(self):
        provider = self._flaky_provider(failures=1)
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            messages, terminal = _run(
                run_query(_make_params(self.workspace, provider))
            )
        self.assertEqual(terminal.reason, "completed")
        warnings = [
            m for m in messages
            if isinstance(m, SystemMessage)
            and getattr(m, "subtype", None) == "api_retry"
        ]
        self.assertEqual(len(warnings), 1)
        self.assertIn("attempt 1/3", str(warnings[0].content))

    def test_gives_up_after_three_retries(self):
        provider = self._flaky_provider(failures=10)
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            messages, terminal = _run(
                run_query(_make_params(self.workspace, provider))
            )
        self.assertEqual(terminal.reason, "model_error")
        warnings = [
            m for m in messages
            if isinstance(m, SystemMessage)
            and getattr(m, "subtype", None) == "api_retry"
        ]
        self.assertEqual(len(warnings), MAX_529_RETRIES)
        self.assertEqual(provider._calls["n"], MAX_529_RETRIES + 1)

    def test_background_source_bails_immediately(self):
        provider = self._flaky_provider(failures=1)
        _messages, terminal = _run(
            run_query(
                _make_params(self.workspace, provider, query_source="compact")
            )
        )
        self.assertEqual(terminal.reason, "model_error")
        self.assertEqual(provider._calls["n"], 1)
        self.assertNotIn("compact", FOREGROUND_529_RETRY_SOURCES)

    def test_no_retry_after_partial_output(self):
        provider = mock.MagicMock()
        provider.model = "claude-test"
        calls = {"n": 0}

        def stream(messages, on_text_chunk=None, **k):
            calls["n"] += 1
            if on_text_chunk is not None:
                on_text_chunk("partial text…")
            raise _Overloaded("529 mid-stream")

        provider.chat_stream_response.side_effect = stream
        chunks: list[str] = []
        _messages, terminal = _run(
            run_query(
                _make_params(
                    self.workspace, provider, on_text_chunk=chunks.append
                )
            )
        )
        self.assertEqual(terminal.reason, "model_error")
        self.assertEqual(calls["n"], 1)  # no second attempt
        self.assertEqual(chunks, ["partial text…"])  # rendered once

# ---------------------------------------------------------------------------
# G4 — client laziness
# ---------------------------------------------------------------------------


class TestClientLaziness(unittest.TestCase):
    def test_anthropic_client_not_built_until_first_call(self):
        from src.providers import anthropic_provider as ap

        provider = ap.AnthropicProvider(api_key="k")
        self.assertIsNone(provider.client)
        fake_sdk = mock.MagicMock()
        with mock.patch.object(ap, "anthropic", fake_sdk, create=True):
            fake_sdk.Anthropic.assert_not_called()
            provider._ensure_client()
            fake_sdk.Anthropic.assert_called_once()

    def test_openai_compatible_client_lazy(self):
        from src.providers.openai_compatible import OpenAICompatibleProvider

        created = {"n": 0}

        class _Concrete(OpenAICompatibleProvider):
            def _create_client(self):
                created["n"] += 1
                return mock.MagicMock()

            def get_available_models(self):
                return []

        provider = _Concrete(api_key="k")
        self.assertIsNone(provider._client)
        self.assertEqual(created["n"], 0)  # not built at __init__
        _ = provider.client  # first touch builds
        self.assertEqual(created["n"], 1)
        _ = provider.client  # cached thereafter
        self.assertEqual(created["n"], 1)




class TestCompactionCostRecording(unittest.TestCase):
    """The test the implementation-critic demanded: compaction recording
    must use the CompactContext (an unbound-name bug at the call sites
    silently demoted successful summarizes into fallback paths)."""

    def setUp(self):
        reset_state_for_tests()

    tearDown = setUp

    def test_compaction_records_and_summary_survives(self):
        from src.services.compact.compact import (
            CompactContext,
            compact_conversation,
        )
        from src.types.messages import AssistantMessage

        provider = mock.MagicMock()
        provider.model = "main-model"

        async def chat_async(*a, **k):
            return ChatResponse(
                content="A long, perfectly valid summary of the session.",
                model="summarize-model",
                usage={"input_tokens": 11, "output_tokens": 4},
                finish_reason="end_turn",
                tool_uses=None,
            )

        provider.chat_async = chat_async
        context = CompactContext(
            provider=provider,
            model="summarize-model",
            messages=[
                UserMessage(content="hello " * 50),
                AssistantMessage(content="world " * 50),
                UserMessage(content="more " * 50),
                AssistantMessage(content="words " * 50),
            ],
        )
        result = _run(compact_conversation(context))
        # Recording happened under the SUMMARIZE model...
        usage = get_model_usage().get("summarize-model")
        self.assertIsNotNone(usage)
        self.assertEqual(usage.input_tokens, 11)
        # ...and the successful summarize was NOT demoted to a fallback.
        self.assertIsNotNone(result)
        self.assertTrue(result.summary_messages)


class TestAdvisorCostRecording(unittest.TestCase):
    def setUp(self):
        reset_state_for_tests()

    tearDown = setUp

    def test_advisor_call_records_usage(self):
        from src.utils import advisor as advisor_mod

        provider = mock.MagicMock()
        provider.model = "advisor-model"
        provider.chat_stream_response.return_value = ChatResponse(
            content="Advice: looks good.",
            model="advisor-model",
            usage={"input_tokens": 21, "output_tokens": 9},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider_cls = mock.Mock(return_value=provider)
        # execute_client_advisor builds its provider from the config
        # providers map (multi-provider routing) — stub both lookups.
        with mock.patch(
            "src.providers.get_provider_class", return_value=provider_cls
        ), mock.patch(
            "src.config.get_provider_config",
            return_value={"api_key": "k", "default_model": "advisor-model"},
        ):
            ok, _text, _usage = advisor_mod.execute_client_advisor(
                "advisor-model",
                [{"role": "user", "content": "review this"}],
                advisor_provider="testprov",
            )
        self.assertTrue(ok)
        recorded = get_model_usage().get("advisor-model")
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.input_tokens, 21)
        self.assertEqual(recorded.output_tokens, 9)


class TestWireLevelRequestShape(unittest.TestCase):
    """Replace the __class__-swap duck-spec (critic) with a real provider
    whose instance methods are stubbed; assert BOTH the resolved
    max_tokens (G0, wire-level) and sdk_max_retries=0 (G3) reach it."""

    def setUp(self):
        reset_state_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        reset_state_for_tests()

    def test_default_request_carries_resolved_max_tokens_and_zero_retries(self):
        import os

        from src.providers.anthropic_provider import AnthropicProvider

        os.environ.pop("CLAUDE_CODE_MAX_OUTPUT_TOKENS", None)
        provider = AnthropicProvider(api_key="k", model="unknown-model-xyz")
        seen: dict = {}

        def chat(*a, **k):
            seen.update(k)
            return _completion()

        provider.chat = mock.Mock(side_effect=chat)
        provider.chat_stream_response = mock.Mock(
            side_effect=NotImplementedError()
        )

        _run(run_query(_make_params(self.workspace, provider)))
        self.assertEqual(seen.get("max_tokens"), DEFAULT_MAX_OUTPUT_TOKENS)
        self.assertEqual(seen.get("sdk_max_retries"), 0)


class TestExtraHeadersReachCreate(unittest.TestCase):
    def test_request_id_reaches_messages_create(self):
        import os

        from src.providers import anthropic_provider as ap

        os.environ.pop("ANTHROPIC_BASE_URL", None)
        provider = ap.AnthropicProvider(api_key="k")
        fake_client = mock.MagicMock()
        fake_client.messages.create.return_value = mock.MagicMock(content=[])
        provider.client = fake_client

        provider.chat([{"role": "user", "content": "hi"}])
        _, kwargs = fake_client.messages.create.call_args
        headers = kwargs.get("extra_headers") or {}
        self.assertIn("x-client-request-id", headers)


if __name__ == "__main__":
    unittest.main()

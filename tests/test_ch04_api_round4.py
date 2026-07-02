"""ch04 round-4 acceptance tests: message cache marker, retry-lane widening,
model fallback e2e, boundary strip + global-scope beta, watchdog warning.

Covers my-docs/port-improvement-round-4/ch04-api-layer-round4-plan.md.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.query.query import (
    DEFAULT_MAX_RETRIES,
    MAX_529_RETRIES,
    PROMPT_CACHING_SCOPE_BETA_HEADER,
    QueryParams,
    _strip_block_metadata,
    run_query,
)
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import SystemMessage, UserMessage
from src.utils.abort_controller import AbortController


def _run(coro):
    return asyncio.run(coro)


def _make_params(*, workspace, provider, fallback_model=None, max_turns=10):
    registry = build_default_registry()
    context = ToolContext(workspace_root=workspace)
    return QueryParams(
        messages=[UserMessage(content="Hi")],
        system_prompt="You are helpful.",
        tools=registry.list_tools(),
        tool_registry=registry,
        tool_use_context=context,
        provider=provider,
        abort_controller=AbortController(),
        max_turns=max_turns,
        fallback_model=fallback_model,
    )


def _completion(content="Done.", model="test-model"):
    return ChatResponse(
        content=content,
        model=model,
        usage={"input_tokens": 10, "output_tokens": 5},
        finish_reason="end_turn",
        tool_uses=None,
    )


class _Err(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status_code = status


class _FlakyProvider:
    """Scripted provider: raises the queued exceptions, then succeeds."""

    def __init__(self, errors, response=None):
        self._errors = list(errors)
        self._response = response or _completion()
        self.model = "primary-model"
        self.calls = 0
        self.models_seen: list[str] = []

    def chat_stream_response(self, *a, **k):
        self.calls += 1
        self.models_seen.append(self.model)
        if self._errors:
            raise self._errors.pop(0)
        return self._response


class TestRetryLane(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        # Retries sleep 0.5s+ with jitter — patch to keep tests fast.
        self._sleep = patch("src.query.query.asyncio.sleep", new=_fast_sleep)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        self._tmp.cleanup()

    def test_500_then_success_retries_with_status(self):
        provider = _FlakyProvider([_Err("internal error", 500)])
        params = _make_params(workspace=self.ws, provider=provider)
        messages, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(provider.calls, 2)
        statuses = [
            m for m in messages
            if isinstance(m, SystemMessage) and m.subtype == "api_retry"
        ]
        self.assertEqual(len(statuses), 1)
        self.assertIn("API error", str(statuses[0].content))

    def test_429_then_success_retries(self):
        provider = _FlakyProvider([_Err("rate limited", 429)])
        params = _make_params(workspace=self.ws, provider=provider)
        _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(provider.calls, 2)

    def test_non_retryable_fails_immediately(self):
        provider = _FlakyProvider([_Err("invalid request", 400)])
        params = _make_params(workspace=self.ws, provider=provider)
        _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "model_error")
        self.assertEqual(provider.calls, 1)

    def test_general_budget_exhausts(self):
        provider = _FlakyProvider([_Err("boom", 503)] * (DEFAULT_MAX_RETRIES + 5))
        params = _make_params(workspace=self.ws, provider=provider)
        _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "model_error")
        self.assertEqual(provider.calls, DEFAULT_MAX_RETRIES + 1)

    def test_529_exhaustion_without_fallback_is_model_error(self):
        provider = _FlakyProvider([_Err("overloaded_error", 529)] * 10)
        params = _make_params(workspace=self.ws, provider=provider)
        messages, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "model_error")
        # 3 retries + the final failing attempt.
        self.assertEqual(provider.calls, MAX_529_RETRIES + 1)
        retry_msgs = [
            m for m in messages
            if isinstance(m, SystemMessage) and m.subtype == "api_retry"
        ]
        self.assertEqual(len(retry_msgs), MAX_529_RETRIES)

    def test_529_storm_switches_to_fallback_model(self):
        provider = _FlakyProvider(
            [_Err("overloaded_error", 529)] * MAX_529_RETRIES,
            response=_completion(model="fallback-model"),
        )
        params = _make_params(
            workspace=self.ws, provider=provider, fallback_model="fallback-model",
        )
        messages, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(provider.model, "fallback-model")
        # The successful call ran under the fallback model.
        self.assertEqual(provider.models_seen[-1], "fallback-model")
        fallback_msgs = [
            m for m in messages
            if isinstance(m, SystemMessage) and m.subtype == "model_fallback"
        ]
        self.assertEqual(len(fallback_msgs), 1)
        self.assertIn("Switched to fallback-model", str(fallback_msgs[0].content))
        self.assertIn("high demand for primary-model", str(fallback_msgs[0].content))

    def test_fallback_fires_once_then_exhausts(self):
        provider = _FlakyProvider([_Err("overloaded_error", 529)] * 20)
        params = _make_params(
            workspace=self.ws, provider=provider, fallback_model="fallback-model",
        )
        messages, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "model_error")
        fallback_msgs = [
            m for m in messages
            if isinstance(m, SystemMessage) and m.subtype == "model_fallback"
        ]
        self.assertEqual(len(fallback_msgs), 1)  # single-shot
        # 3 on primary + fallback switch + 3 on fallback + final failure.
        self.assertEqual(provider.calls, 2 * MAX_529_RETRIES + 1)

    def test_background_source_does_not_retry(self):
        provider = _FlakyProvider([_Err("boom", 503)])
        params = _make_params(workspace=self.ws, provider=provider)
        params.query_source = "compact"
        _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "model_error")
        self.assertEqual(provider.calls, 1)


async def _fast_sleep(_seconds):
    return None


class TestBoundaryStripAndBetas(unittest.TestCase):
    def test_strip_removes_boundary_and_metadata(self):
        from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY

        blocks = [
            {"type": "text", "text": "static", "_cache_scope": "GLOBAL",
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": SYSTEM_PROMPT_DYNAMIC_BOUNDARY},
            {"type": "text", "text": "dynamic", "_cache_scope": "SESSION"},
        ]
        cleaned = _strip_block_metadata(blocks)
        texts = [b["text"] for b in cleaned]
        self.assertNotIn(SYSTEM_PROMPT_DYNAMIC_BOUNDARY, texts)
        self.assertEqual(len(cleaned), 2)
        self.assertTrue(all("_cache_scope" not in b for b in cleaned))
        # cache_control survives the strip.
        self.assertIn("cache_control", cleaned[0])

    def test_global_scope_beta_constant_matches_ts(self):
        self.assertEqual(
            PROMPT_CACHING_SCOPE_BETA_HEADER, "prompt-caching-scope-2026-01-05",
        )

    def _capture_call_kwargs(self, system_prompt):
        """Drive _call_model_sync with a spy provider; return its kwargs."""
        from src.query.query import _call_model_sync
        from src.providers.anthropic_provider import AnthropicProvider

        provider = MagicMock(spec=AnthropicProvider)
        provider.model = "claude-sonnet-4-6"
        provider.is_deepseek = False
        captured: dict = {}

        def _chat_stream(messages, **kwargs):
            captured.update(kwargs)
            return _completion()

        provider.chat_stream_response.side_effect = _chat_stream
        _run(_call_model_sync(
            provider=provider,
            messages=[UserMessage(content="hi")],
            system_prompt=system_prompt,
            tools=[],
            max_output_tokens_override=None,
            abort_signal=AbortController().signal,
        ))
        return captured

    def test_global_scope_block_appends_beta(self):
        captured = self._capture_call_kwargs([
            {"type": "text", "text": "static",
             "cache_control": {"type": "ephemeral", "scope": "global"}},
            {"type": "text", "text": "dynamic"},
        ])
        self.assertIn(PROMPT_CACHING_SCOPE_BETA_HEADER, captured.get("betas", []))

    def test_no_scope_no_beta(self):
        captured = self._capture_call_kwargs([
            {"type": "text", "text": "static",
             "cache_control": {"type": "ephemeral"}},
        ])
        self.assertNotIn(
            PROMPT_CACHING_SCOPE_BETA_HEADER, captured.get("betas", []) or [],
        )


class TestMessageCacheMarker(unittest.TestCase):
    """GAP A — the live Anthropic streaming lane marks the last message."""

    def _capture_request(self, messages):
        from src.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
        captured: dict = {}

        class _FakeStreamCtx:
            def __enter__(self):
                raise NotImplementedError("stop before network")

            def __exit__(self, *a):
                return False

        fake_client = MagicMock()

        def _stream(**kwargs):
            captured.update(kwargs)
            return _FakeStreamCtx()

        fake_client.messages.stream.side_effect = _stream
        with patch.object(provider, "_client_for_request", return_value=fake_client):
            try:
                provider.chat_stream_response(messages, max_tokens=100)
            except Exception:
                pass
        return captured

    def test_last_message_carries_single_marker(self):
        captured = self._capture_request([
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
        ])
        msgs = captured.get("messages")
        self.assertIsNotNone(msgs)
        marker_count = 0
        for m in msgs:
            content = m.get("content")
            if isinstance(content, list):
                marker_count += sum(
                    1 for b in content
                    if isinstance(b, dict) and "cache_control" in b
                )
        self.assertEqual(marker_count, 1)
        last_content = msgs[-1]["content"]
        self.assertIsInstance(last_content, list)
        self.assertIn("cache_control", last_content[-1])

    def test_trailing_thinking_block_skips_marker(self):
        """critic NIT-1: thinking blocks reject cache_control — the marker
        is skipped entirely rather than 400-ing the turn."""
        from src.services.api.claude import add_cache_breakpoints

        out = add_cache_breakpoints([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "reasoned"},
                {"type": "thinking", "thinking": "...", "signature": "s"},
            ]},
        ])
        for m in out:
            content = m.get("content")
            if isinstance(content, list):
                for b in content:
                    self.assertNotIn("cache_control", b)

    def test_chat_one_shot_stays_unmarked(self):
        """Internal one-shots (compaction etc.) use chat(); no marker —
        a cache WRITE on a never-reused prefix costs a premium for nothing."""
        from src.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
        captured: dict = {}
        fake_client = MagicMock()

        def _create(**kwargs):
            captured.update(kwargs)
            raise NotImplementedError("stop before network")

        fake_client.messages.create.side_effect = _create
        with patch.object(provider, "_client_for_request", return_value=fake_client):
            try:
                provider.chat([{"role": "user", "content": "hi"}], max_tokens=10)
            except Exception:
                pass
        for m in captured.get("messages", []):
            content = m.get("content")
            if isinstance(content, list):
                for b in content:
                    self.assertNotIn("cache_control", b)


class TestWatchdogWarning(unittest.TestCase):
    def test_half_time_warning_fires_once_and_reset_cancels(self):
        from src.utils.stream_watchdog import StreamWatchdog

        stream = MagicMock()
        wd = StreamWatchdog(stream, timeout_s=0.2, request_id="req-1")
        with self.assertLogs("src.utils.stream_watchdog", level="WARNING") as logs:
            wd.arm()
            import time

            time.sleep(0.13)  # past half (0.1), before full (0.2)
            wd.disarm()
        self.assertEqual(len(logs.output), 1)
        self.assertIn("req-1", logs.output[0])

    def test_reset_before_half_time_prevents_warning(self):
        from src.utils.stream_watchdog import StreamWatchdog

        stream = MagicMock()
        wd = StreamWatchdog(stream, timeout_s=0.3)
        import time

        # critic m3: assert the warning is actually SUPPRESSED, not just
        # that the deadline never fired.
        with self.assertNoLogs("src.utils.stream_watchdog", level="WARNING"):
            wd.arm()
            time.sleep(0.1)
            wd.reset()  # pushes the half-time point back
            time.sleep(0.1)
            wd.disarm()
        self.assertFalse(wd.fired)


if __name__ == "__main__":
    unittest.main()

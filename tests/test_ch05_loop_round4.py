"""ch05 round-4 acceptance tests: the production compaction pipeline wire,
the +500k budget thread, and the abort-path max-turns attachment.

Covers my-docs/port-improvement-round-4/ch05-agent-loop-round4-plan.md.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.query.query import (
    FOREGROUND_529_RETRY_SOURCES,
    QueryParams,
    run_query,
)
from src.services.compact.autocompact import AutoCompactTracking
from src.services.compact.pipeline import (
    PipelineConfig,
    build_production_pipeline_config,
)
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import SystemMessage, UserMessage
from src.utils.abort_controller import AbortController


def _run(coro):
    return asyncio.run(coro)


def _completion(content="Done."):
    return ChatResponse(
        content=content,
        model="test-model",
        usage={"input_tokens": 10, "output_tokens": 5},
        finish_reason="end_turn",
        tool_uses=None,
    )


class TestPipelineConfigBuilder(unittest.TestCase):
    def test_mirrors_engine_shape(self):
        provider = MagicMock()
        provider.model = "m1"
        context = MagicMock()
        context.read_file_fingerprints = {Path("/tmp/a.py"): (123.0, 42)}
        tracking = AutoCompactTracking()

        cfg = build_production_pipeline_config(provider, context, tracking)

        self.assertIsInstance(cfg, PipelineConfig)
        self.assertIs(cfg.provider, provider)
        self.assertEqual(cfg.model, "m1")
        self.assertEqual(cfg.read_file_state, {"/tmp/a.py": {"timestamp": 123.0}})
        self.assertIs(cfg.autocompact_tracking, tracking)

    def test_empty_fingerprints_yield_none_state(self):
        provider = MagicMock()
        provider.model = "m1"
        context = MagicMock()
        context.read_file_fingerprints = {}
        cfg = build_production_pipeline_config(
            provider, context, AutoCompactTracking(),
        )
        self.assertIsNone(cfg.read_file_state)


class TestAdapterThreading(unittest.TestCase):
    """The adapter forwards pipeline_config / query_source / token_budget."""

    def test_params_reach_query(self):
        from src.query import agent_loop_compat as compat

        captured: dict = {}
        real_qp = compat.QueryParams

        def _spy_params(**kwargs):
            captured.update(kwargs)
            return real_qp(**kwargs)

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion()
        registry = build_default_registry()
        with tempfile.TemporaryDirectory() as tmp:
            context = ToolContext(workspace_root=Path(tmp))
            cfg = PipelineConfig(provider=provider, model="m")
            with patch.object(compat, "QueryParams", side_effect=_spy_params):
                _run(compat.run_query_as_agent_loop(
                    initial_messages=[UserMessage(content="hi")],
                    provider=provider,
                    tool_registry=registry,
                    tool_context=context,
                    system_prompt="You are helpful.",
                    max_turns=2,
                    pipeline_config=cfg,
                    query_source="sdk",
                    token_budget=500_000,
                ))
        self.assertIs(captured.get("pipeline_config"), cfg)
        self.assertEqual(captured.get("query_source"), "sdk")
        self.assertEqual(captured.get("token_budget"), 500_000)

    def test_sdk_is_a_foreground_retry_source(self):
        # The headless relabel must not lose the retry lane (TS
        # withRetry.ts:67 includes 'sdk').
        self.assertIn("sdk", FOREGROUND_529_RETRY_SOURCES)
        self.assertIn("repl_main_thread", FOREGROUND_529_RETRY_SOURCES)


class TestPipelineRunsInLoop(unittest.TestCase):
    """With a pipeline_config present, the loop invokes the pipeline."""

    def test_pipeline_invoked_when_config_present(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion()
        registry = build_default_registry()
        with tempfile.TemporaryDirectory() as tmp:
            context = ToolContext(workspace_root=Path(tmp))
            cfg = PipelineConfig(provider=provider, model="test-model")
            params = QueryParams(
                messages=[UserMessage(content="hi")],
                system_prompt="You are helpful.",
                tools=registry.list_tools(),
                tool_registry=registry,
                tool_use_context=context,
                provider=provider,
                abort_controller=AbortController(),
                max_turns=2,
                pipeline_config=cfg,
            )
            from src.services.compact.pipeline import CompressionResult

            with patch(
                "src.query.query.run_compression_pipeline",
            ) as pipeline_spy:
                # critic m3: a REAL typed result (tokens_saved=0) so the
                # loop's `.tokens_saved` read means what it says instead of
                # hitting a truthy MagicMock auto-attribute.
                pipeline_spy.return_value = CompressionResult(
                    messages=list(params.messages), tokens_saved=0,
                )
                _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "completed")
        pipeline_spy.assert_called()

    def test_pipeline_skipped_without_config(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion()
        registry = build_default_registry()
        with tempfile.TemporaryDirectory() as tmp:
            context = ToolContext(workspace_root=Path(tmp))
            params = QueryParams(
                messages=[UserMessage(content="hi")],
                system_prompt="You are helpful.",
                tools=registry.list_tools(),
                tool_registry=registry,
                tool_use_context=context,
                provider=provider,
                abort_controller=AbortController(),
                max_turns=2,
            )
            with patch(
                "src.query.query.run_compression_pipeline",
            ) as pipeline_spy:
                _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "completed")
        pipeline_spy.assert_not_called()


class TestServerSessionTracking(unittest.TestCase):
    """The agent-server session owns ONE tracking instance across turns —
    exercised through the PRODUCTION config-build block (critic M2: the
    prior version re-implemented the pattern in the test body)."""

    def _make_session(self):
        from src.server.agent_server import AgentServerConfig, _AgentSession

        sess = _AgentSession(
            session_id="s1",
            cwd="/tmp",
            config=AgentServerConfig(single_session=True),
            loop=MagicMock(),
            out_queue=MagicMock(),
        )
        sess.tool_context = MagicMock()
        sess.tool_context.read_file_fingerprints = {}
        return sess

    def test_two_turn_builds_share_one_tracking_instance(self):
        sess = self._make_session()
        provider = MagicMock()
        provider.model = "m"
        self.assertIsNone(sess._auto_compact_tracking)

        cfg1 = sess._build_turn_pipeline_config(provider)
        cfg2 = sess._build_turn_pipeline_config(provider)

        self.assertIsNotNone(cfg1)
        self.assertIsNotNone(cfg2)
        self.assertIsNot(cfg1, cfg2)  # fresh config each turn
        self.assertIs(
            cfg1.autocompact_tracking, cfg2.autocompact_tracking,
        )  # SAME breaker across turns
        self.assertIs(cfg1.autocompact_tracking, sess._auto_compact_tracking)

    def test_build_failure_returns_none(self):
        sess = self._make_session()
        with patch(
            "src.services.compact.pipeline.build_production_pipeline_config",
            side_effect=RuntimeError("boom"),
        ):
            self.assertIsNone(sess._build_turn_pipeline_config(MagicMock()))


class TestTokenBudgetParseInServer(unittest.TestCase):
    def test_parse_token_budget_shorthand(self):
        from src.query.token_budget import parse_token_budget

        self.assertEqual(parse_token_budget("+500k fix the bug"), 500_000)
        self.assertEqual(parse_token_budget("fix the bug +500k"), 500_000)
        self.assertIsNone(parse_token_budget("fix the bug"))

    def test_server_parses_original_prompt_shapes(self):
        """critic m1 — the server parses the ORIGINAL prompt (str or block
        list) BEFORE ultracode appends a reminder; an end-anchored '+500k'
        must survive. The ordering itself is pinned by parsing the raw
        prompt through the production helper, then showing the augmented
        form would NOT match."""
        from src.server.agent_server import _AgentSession, _with_ultracode_reminder

        raw = "fix the bug +500k"
        self.assertEqual(_AgentSession._parse_turn_budget(raw), 500_000)
        blocks = [{"type": "text", "text": raw}]
        self.assertEqual(_AgentSession._parse_turn_budget(blocks), 500_000)
        # The augmented prompt (reminder APPENDED) breaks the end anchor —
        # exactly why _run_turn parses before augmentation.
        augmented = f"{raw}\n\n<system-reminder>ultracode</system-reminder>"
        self.assertIsNone(_AgentSession._parse_turn_budget(augmented))


class TestAbortPathMaxTurnsAttachment(unittest.TestCase):
    def test_abort_at_limit_yields_attachment(self):
        from src.services.tool_execution.orchestrator import MessageUpdate
        from src.types.content_blocks import ToolResultBlock

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Working...",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_a1",
                "name": "Write",
                "input": {"file_path": "/tmp/x.txt", "content": "hi"},
            }],
        )
        registry = build_default_registry()
        with tempfile.TemporaryDirectory() as tmp:
            context = ToolContext(workspace_root=Path(tmp))
            abort = AbortController()
            params = QueryParams(
                messages=[UserMessage(content="hi")],
                system_prompt="You are helpful.",
                tools=registry.list_tools(),
                tool_registry=registry,
                tool_use_context=context,
                provider=provider,
                abort_controller=abort,
                max_turns=1,
            )

            async def _abort_during_tools(_b, _a, _c, ctx, *args, **kwargs):
                abort.abort("test_abort")
                yield MessageUpdate(
                    message=UserMessage(content=[ToolResultBlock(
                        tool_use_id="toolu_a1", content="ok", is_error=False,
                    )]),
                    new_context=ctx,
                )

            with patch(
                "src.services.tool_execution.orchestrator.run_tools",
                new=_abort_during_tools,
            ):
                messages, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "aborted_tools")
        max_turn_msgs = [
            m for m in messages
            if isinstance(m, SystemMessage)
            and getattr(m, "subtype", "") == "max_turns_reached"
        ]
        self.assertEqual(len(max_turn_msgs), 1)


if __name__ == "__main__":
    unittest.main()

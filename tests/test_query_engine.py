import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import TextBlock
from src.types.messages import AssistantMessage, UserMessage
from src.utils.abort_controller import AbortController

from src.query.engine import QueryEngine, QueryEngineConfig
from src.query.query import StreamEvent


def _run(coro):
    return asyncio.run(coro)


class TestQueryEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_engine(self, provider) -> QueryEngine:
        tools = self.registry.list_tools()
        config = QueryEngineConfig(
            cwd=self.workspace,
            provider=provider,
            tool_registry=self.registry,
            tools=tools,
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=10,
        )
        return QueryEngine(config)

    def test_submit_message_yields_assistant(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Test response",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        engine = self._make_engine(provider)
        collected = []

        async def run():
            async for msg in engine.submit_message("Hello"):
                collected.append(msg)

        _run(run())

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertEqual(len(assistants), 1)

    def test_messages_accumulate(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Response",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        engine = self._make_engine(provider)

        async def run():
            async for _ in engine.submit_message("First"):
                pass

        _run(run())

        msgs = engine.get_messages()
        user_msgs = [m for m in msgs if isinstance(m, UserMessage)]
        assistant_msgs = [m for m in msgs if isinstance(m, AssistantMessage)]
        self.assertGreaterEqual(len(user_msgs), 1)
        self.assertGreaterEqual(len(assistant_msgs), 1)

    def test_interrupt(self):
        engine = self._make_engine(MagicMock())
        engine.interrupt()

    def test_reset_abort_controller(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Response",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        engine = self._make_engine(provider)
        engine.interrupt()
        engine.reset_abort_controller()

        collected = []

        async def run():
            async for msg in engine.submit_message("Hello again"):
                collected.append(msg)

        _run(run())

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertGreaterEqual(len(assistants), 1)

    def test_session_id_exists(self):
        engine = self._make_engine(MagicMock())
        self.assertIsInstance(engine.session_id, str)
        self.assertGreater(len(engine.session_id), 0)

    def test_last_terminal_starts_none(self):
        """Ch5/A follow-up: before any submit_message, last_terminal is None."""
        engine = self._make_engine(MagicMock())
        self.assertIsNone(engine.last_terminal)

    def test_total_usage_accumulates_per_turn(self):
        """Critic-flagged: engine.total_usage must accumulate
        input_tokens, output_tokens, cache_creation_input_tokens,
        cache_read_input_tokens per turn — pre-existing gap before
        the cache-token follow-up."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Done.",
            model="test",
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 1000,
                "cache_read_input_tokens": 200,
            },
            finish_reason="end_turn",
            tool_uses=None,
        )
        engine = self._make_engine(provider)

        async def run():
            async for _ in engine.submit_message("Hi"):
                pass

        _run(run())

        # All four fields must have accumulated.
        self.assertEqual(engine.total_usage["input_tokens"], 100)
        self.assertEqual(engine.total_usage["output_tokens"], 50)
        self.assertEqual(engine.total_usage["cache_creation_input_tokens"], 1000)
        self.assertEqual(engine.total_usage["cache_read_input_tokens"], 200)

    def test_last_terminal_set_after_submit_message(self):
        """Ch5/A follow-up: after submit_message completes, the engine
        exposes the Terminal so callers can discriminate why the loop
        stopped."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Done.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )
        engine = self._make_engine(provider)

        async def run():
            async for _ in engine.submit_message("Hi"):
                pass

        _run(run())

        self.assertIsNotNone(engine.last_terminal)
        self.assertEqual(engine.last_terminal.reason, "completed")


class TestEngineProducesCacheableSystemBlocks(unittest.TestCase):
    """Joint WI-1.1 + WI-1.2 acceptance: end-to-end engine produces blocks.

    The plan's joint-PR contract: ``cache_control`` markers reach the API
    AND the date string is byte-identical across two consecutive turns.
    Failing either condition means cache hits stay at zero in steady state.

    This test exercises the full engine path (no caller-supplied
    ``system_prompt``, no ``custom_system_prompt``) so the production
    block-list assembly runs end-to-end. Mocks the provider's ``chat``
    method to capture the ``system_prompt`` shape passed via QueryParams.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        # Reset the date lru_cache so tests start with a fresh capture.
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        _get_session_start_date_iso.cache_clear()
        from src.context_system import clear_context_caches
        clear_context_caches()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_engine_no_system_prompt(self, provider):
        """Production path: no system_prompt, no custom_system_prompt → blocks."""
        from src.query.engine import QueryEngine, QueryEngineConfig
        # Use AnthropicProvider so query.py forwards the list shape (rather
        # than flattening for non-Anthropic providers per query.py:251).
        config = QueryEngineConfig(
            cwd=self.workspace,
            provider=provider,
            tool_registry=self.registry,
            tools=self.registry.list_tools(),
            tool_context=self.context,
            system_prompt=None,  # ← production assembly path
            max_turns=10,
        )
        return QueryEngine(config)

    def test_engine_produces_block_list_with_cache_control(self):
        """End-to-end: engine builds the production prompt as block list."""
        from src.providers.anthropic_provider import AnthropicProvider
        # Patch the AnthropicProvider's SDK client so we don't need keys.
        provider = MagicMock(spec=AnthropicProvider)
        provider.model = "claude-sonnet-4-20250514"
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="ok",
            model="claude-sonnet-4-20250514",
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            finish_reason="end_turn",
            tool_uses=None,
        )

        engine = self._make_engine_no_system_prompt(provider)

        async def run():
            async for _ in engine.submit_message("Hello"):
                pass

        _run(run())

        # Inspect the system arg the provider's chat was called with.
        # query.py forwards via call_kwargs["system"] = system_prompt; the
        # provider mock's call_args captures the positional/kwargs shape.
        # We use chat (not chat_stream_response — that path raised
        # NotImplementedError so the engine fell back to non-streaming).
        self.assertTrue(provider.chat.called, "engine must invoke provider.chat")
        call = provider.chat.call_args
        # The provider receives the system kwarg via the QueryParams wiring
        # at query.py:243. Inspect kwargs.
        system_arg = call.kwargs.get("system")
        self.assertIsNotNone(system_arg, "system kwarg must be forwarded")
        self.assertIsInstance(
            system_arg, list,
            f"Expected block-list shape, got {type(system_arg).__name__}",
        )
        # At least one block must carry cache_control.
        marked = [b for b in system_arg if "cache_control" in b]
        self.assertGreaterEqual(
            len(marked), 1,
            "Engine must emit at least one cache_control marker",
        )
        # Boundary literal must be present.
        from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        boundary_blocks = [
            b for b in system_arg if b.get("text") == SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        ]
        self.assertEqual(
            len(boundary_blocks), 1,
            "Engine must emit exactly one boundary-marker block",
        )

    def test_engine_threads_mcp_servers_to_block_assembly(self):
        """Critic Phase 2 M1: engine MUST forward mcp_servers to the block builder.

        Without threading, the WI-2.3 MCP gate (per chapter line 91 — MCP
        schemas are per-user, can't share globally) is bypassed at the
        integration layer: even a session with MCP tools loaded would see
        ``scope: 'global'`` on GLOBAL-tier blocks once the env var flips on.

        This test sets ``CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE=1``, configures
        the engine with a non-empty ``mcp_servers``, drives a turn, captures
        the system arg, and asserts NO block carries ``scope: 'global'``.
        """
        import os
        from src.providers.anthropic_provider import AnthropicProvider
        from src.query.engine import QueryEngine, QueryEngineConfig
        from src.state.cache_state import reset_for_test_only

        reset_for_test_only()
        os.environ["CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE"] = "1"
        try:
            provider = MagicMock(spec=AnthropicProvider)
            provider.model = "claude-sonnet-4-20250514"
            provider.has_custom_endpoint.return_value = False
            provider.chat_stream_response.side_effect = NotImplementedError()
            provider.chat.return_value = ChatResponse(
                content="ok",
                model="claude-sonnet-4-20250514",
                usage={
                    "input_tokens": 10, "output_tokens": 5,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
                finish_reason="end_turn",
                tool_uses=None,
            )

            # Build a fresh engine with mcp_servers populated.
            config = QueryEngineConfig(
                cwd=self.workspace,
                provider=provider,
                tool_registry=self.registry,
                tools=self.registry.list_tools(),
                tool_context=self.context,
                system_prompt=None,
                max_turns=10,
                mcp_servers=[object(), object()],  # ← non-empty
            )
            engine = QueryEngine(config)

            async def run():
                async for _ in engine.submit_message("Hi"):
                    pass

            _run(run())

            # Capture the system arg sent to provider.chat.
            self.assertTrue(provider.chat.called)
            system_arg = provider.chat.call_args.kwargs.get("system")
            self.assertIsInstance(system_arg, list)

            # No block may carry scope='global' when MCP is loaded.
            for blk in system_arg:
                cc = blk.get("cache_control")
                if cc:
                    self.assertNotIn(
                        "scope", cc,
                        "MCP-loaded session must not emit scope='global' — "
                        "WI-2.3 MCP gate must be threaded through the engine",
                    )
        finally:
            os.environ.pop("CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE", None)
            reset_for_test_only()

    def test_boundary_literal_filtered_from_non_anthropic_system_message(self):
        """Critic M1 regression: ``__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`` MUST NOT
        leak into non-Anthropic providers' system prompts.

        The boundary marker is a cache-only signal for the Anthropic backend.
        OpenAI / DeepSeek / GLM / etc. would receive it as raw text mid-prose
        and might interpret it as a control token. The flatten path in
        ``query.py`` filters the boundary block before joining; this test
        asserts that filter is in place.
        """
        from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        from src.providers.openai_provider import OpenAIProvider
        # Use a non-Anthropic provider mock — query.py routes through the
        # flatten path for these.
        provider = MagicMock(spec=OpenAIProvider)
        provider.model = "gpt-4-turbo"
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="ok",
            model="gpt-4-turbo",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop",
            tool_uses=None,
        )

        engine = self._make_engine_no_system_prompt(provider)

        async def run():
            async for _ in engine.submit_message("Hello"):
                pass

        _run(run())

        # Inspect the messages list passed to provider.chat — the system
        # prompt rides as a "role: system" message at index 0 (per
        # query.py:269 for non-Anthropic providers).
        self.assertTrue(provider.chat.called)
        call_kwargs = provider.chat.call_args.kwargs
        # query.py prepends {"role": "system", "content": flattened} to api_messages.
        api_messages = call_kwargs.get("messages") or provider.chat.call_args.args[0]
        sys_msgs = [m for m in api_messages if m.get("role") == "system"]
        self.assertGreater(len(sys_msgs), 0, "expected system message")
        sys_content = sys_msgs[0].get("content", "")
        self.assertNotIn(
            SYSTEM_PROMPT_DYNAMIC_BOUNDARY, sys_content,
            "boundary literal must be filtered before reaching non-Anthropic providers",
        )

    def test_two_turns_produce_byte_identical_cached_blocks(self):
        """Joint contract: two turns produce identical GLOBAL+SESSION blocks.

        If the date isn't memoized (WI-1.2 broken), block contents drift
        on every turn even if the cache_control plumbing (WI-1.1) is sound.
        Both must hold for cache reads to fire.
        """
        from src.providers.anthropic_provider import AnthropicProvider
        provider = MagicMock(spec=AnthropicProvider)
        provider.model = "claude-sonnet-4-20250514"
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="ok",
            model="claude-sonnet-4-20250514",
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            finish_reason="end_turn",
            tool_uses=None,
        )

        engine = self._make_engine_no_system_prompt(provider)

        async def turn(prompt: str):
            async for _ in engine.submit_message(prompt):
                pass

        _run(turn("first"))
        _run(turn("second"))

        # Capture both system args.
        call_args_list = provider.chat.call_args_list
        self.assertGreaterEqual(len(call_args_list), 2)
        first_system = call_args_list[0].kwargs.get("system")
        second_system = call_args_list[1].kwargs.get("system")

        # Both should be lists.
        self.assertIsInstance(first_system, list)
        self.assertIsInstance(second_system, list)

        # The cached portion (everything before the user-input section
        # changes) must be byte-identical. Concretely: every block that
        # carries cache_control should be identical across the two calls.
        first_marked = [b for b in first_system if "cache_control" in b]
        second_marked = [b for b in second_system if "cache_control" in b]
        self.assertEqual(
            first_marked, second_marked,
            "cache_control-marked blocks must be byte-identical across turns",
        )


if __name__ == "__main__":
    unittest.main()

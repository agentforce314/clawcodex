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

    def test_image_unsupported_strips_images_from_mutable_messages(self):
        """When the provider rejects images, the engine must strip
        image blocks from history so the NEXT submit_message() call
        doesn't re-trip the same error on an unrelated text-only
        request. This is the engine-side mirror of the OpenRouter +
        DeepSeek context-stuck loop.

        Pins three invariants:
        1. The user's text intent (TextBlock) survives — only the
           rejected image bytes are dropped.
        2. The placeholder text "[image]" appears so the model can
           still see that an image WAS there.
        3. No ImageBlock instances or dict-shape image blocks remain
           anywhere in get_messages() — including ToolResultBlock-
           nested image content (the realistic Read-tool case).
        """
        from src.types.content_blocks import (
            ImageBlock, TextBlock, ToolResultBlock,
        )

        provider = MagicMock()
        err = Exception(
            "Error code: 404 - {'error': {'message': "
            "'No endpoints found that support image input', 'code': 404}}"
        )
        provider.chat_stream_response.side_effect = err
        provider.chat.side_effect = err

        # Seed initial_messages with a tool_result-nested image (mirrors
        # what the Read tool produces when reading an image file —
        # src/tool_system/tools/read.py emits a ToolResultBlock whose
        # content is a list containing a dict-shape image block). This
        # is the realistic case that produced the original bug, in
        # addition to direct @-mention images.
        nested_image_dict = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "BBBB",
            },
        }
        prior_user_with_nested = UserMessage(content=[
            TextBlock(text="prior turn"),
            ToolResultBlock(
                tool_use_id="tool_use_prior",
                content=[nested_image_dict],
            ),
        ])

        engine = QueryEngine(QueryEngineConfig(
            cwd=self.workspace,
            provider=provider,
            tool_registry=self.registry,
            tools=self.registry.list_tools(),
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=10,
            initial_messages=[prior_user_with_nested],
        ))

        async def run():
            async for _ in engine.submit_message([
                TextBlock(text="describe this picture"),
                ImageBlock(source={
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "AAAA",
                }),
            ]):
                pass

        _run(run())

        msgs = engine.get_messages()
        # Find the last user message we just submitted.
        user_msgs = [m for m in msgs if isinstance(m, UserMessage)]
        self.assertGreaterEqual(len(user_msgs), 2)
        last_user = user_msgs[-1]
        self.assertIsInstance(last_user.content, list)

        text_blocks = [
            b for b in last_user.content
            if isinstance(b, TextBlock)
        ]
        # (1) text intent preserved
        texts = [b.text for b in text_blocks]
        self.assertIn("describe this picture", texts)
        # (2) placeholder present
        self.assertIn("[image]", texts)

        # (3) no ImageBlock instances or dict-shape image blocks
        # remain anywhere in get_messages — INCLUDING nested in
        # ToolResultBlock.content. Recurses one level deep to mirror
        # the realistic Read-tool case (a deeper nesting walker is
        # bounded by strip_images_from_typed_messages itself; one
        # level is the production shape).
        def assert_no_images_in(blocks: list, where: str) -> None:
            for block in blocks:
                self.assertNotIsInstance(
                    block, ImageBlock,
                    f"ImageBlock must be stripped after image_unsupported error ({where})",
                )
                if isinstance(block, dict):
                    self.assertNotEqual(
                        block.get("type"), "image",
                        f"dict-shape image block must be stripped ({where})",
                    )
                # Recurse into ToolResultBlock.content
                if isinstance(block, ToolResultBlock) and isinstance(block.content, list):
                    assert_no_images_in(block.content, f"{where} > tool_result")

        for i, m in enumerate(msgs):
            if isinstance(m.content, list):
                assert_no_images_in(m.content, f"message[{i}]")

    def test_image_unsupported_error_does_not_recur_on_next_submit(self):
        """End-to-end regression for the user's reported bug: after the
        image-unsupported error fires, a second submit_message() with a
        text-only prompt must reach the provider WITHOUT any image
        blocks in the messages payload. The strip step is what fixes
        the original context-stuck loop, so the test must inspect the
        actual provider call args (not just count) to demonstrate it."""
        from src.types.content_blocks import ImageBlock, TextBlock

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        # First call: provider rejects the image.
        # Second call: provider succeeds (because images are stripped).
        success = ChatResponse(
            content="Here is your blog app plan...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 8},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [
            Exception("No endpoints found that support image input"),
            success,
        ]

        engine = self._make_engine(provider)

        async def run():
            # Turn 1: image-bearing message → error.
            async for _ in engine.submit_message([
                TextBlock(text="describe"),
                ImageBlock(source={
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "AAAA",
                }),
            ]):
                pass
            # Turn 2: unrelated text-only request — MUST succeed.
            collected_text: list[str] = []
            async for m in engine.submit_message("create a blog app"):
                if isinstance(m, AssistantMessage):
                    if isinstance(m.content, str):
                        collected_text.append(m.content)
                    elif isinstance(m.content, list):
                        for b in m.content:
                            if isinstance(b, TextBlock):
                                collected_text.append(b.text)
            return collected_text

        result = _run(run())
        self.assertIn("Here is your blog app plan...", result,
                      "second turn must succeed after image strip")
        # Provider was called twice (turn 1 errored, turn 2 succeeded).
        self.assertEqual(provider.chat.call_count, 2)

        # The bug fix is meaningful only if turn 2's API call carries
        # NO image content. Inspect the actual messages payload sent
        # to provider.chat on the second call. A regression that
        # left the image in history would show up here as an
        # ImageBlock instance or {"type": "image"} dict, even though
        # the call still returned `success` because the mock is
        # unconditional. This pins the real invariant: the wire
        # payload no longer carries the offending image.
        second_call_kwargs = provider.chat.call_args_list[1].kwargs
        second_call_args = provider.chat.call_args_list[1].args
        # The first positional arg or `messages=` kwarg holds the list.
        messages_arg = (
            second_call_kwargs.get("messages")
            if "messages" in second_call_kwargs
            else (second_call_args[0] if second_call_args else None)
        )
        self.assertIsNotNone(messages_arg, "provider.chat must be called with messages")

        def _collect_images(content, into):
            if not isinstance(content, list):
                return
            for block in content:
                if isinstance(block, ImageBlock):
                    into.append(block)
                    continue
                if isinstance(block, dict) and block.get("type") == "image":
                    into.append(block)
                    continue
                # Recurse into tool_result content (dict or typed).
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    _collect_images(block.get("content"), into)
                inner = getattr(block, "content", None)
                if isinstance(inner, list):
                    _collect_images(inner, into)

        leaked: list = []
        for m in messages_arg:
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            _collect_images(content, leaked)
        self.assertEqual(
            leaked, [],
            "no image blocks may reach the provider on the post-strip turn; "
            f"found {len(leaked)} leaked image block(s): {leaked!r}",
        )


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
        # ch04 round-4 GAP C: the boundary is a SPLIT SIGNAL, never wire
        # content (TS splitSysPromptPrefix skips it, utils/api.ts:388/424).
        # The forwarded system arg must NOT contain the literal.
        from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        boundary_blocks = [
            b for b in system_arg if b.get("text") == SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        ]
        self.assertEqual(
            len(boundary_blocks), 0,
            "The boundary literal must never reach the wire",
        )

    def test_engine_wires_skill_listing_into_system_prompt(self):
        """P0-4 end-to-end: a discoverable project skill reaches the model.

        The unit tests in ``tests/test_aggregator.py`` prove the two filtered
        views compute the right set, and ``tests/test_prompt_assembly.py``
        proves ``_build_skill_section`` renders a listing. But neither
        exercises the actual engine wiring at ``engine.py`` —
        ``skills=get_skill_tool_commands(cwd)``. A regression that drops the
        ``skills=`` kwarg, threads the wrong ``cwd``, or calls the slash-command
        view instead would pass every one of those unit tests yet silently stop
        advertising skills to the model.

        This plants a real project skill under the engine's ``cwd``, drives one
        turn, and asserts the assembled system-prompt block list contains the
        ``# Available Skills`` listing with the fixture skill — closing the gap.
        """
        from src.command_system.aggregator import clear_commands_cache
        from src.context_system.prompt_assembly import get_system_prompt_cache
        from src.providers.anthropic_provider import AnthropicProvider
        from src.skills.create import create_skill

        # Plant a project skill discoverable from the engine's cwd:
        # <cwd>/.clawcodex/skills/<name>/SKILL.md → loaded_from="project", which
        # is in SKILLS_DIR_BUCKET so get_skill_tool_commands includes it.
        create_skill(
            directory=self.workspace / ".clawcodex" / "skills",
            name="wiringdemo",
            description="a wiring demo skill",
            body="Hello from the wiring demo.",
        )
        # Refresh BOTH cache layers (see clear_commands_cache's docstring): the
        # command-aggregation cwd cache AND the prompt-assembly session cache
        # that holds the rendered "# Available Skills" prose. The latter is
        # SESSION-scoped and keyed only by section-id (NOT cwd), so an earlier
        # cold-start engine test in this run would otherwise serve its own
        # (wiringdemo-less) listing here. Clean up after ourselves so a later
        # test isn't served OUR wiringdemo listing.
        skill_cache = get_system_prompt_cache()
        clear_commands_cache()
        skill_cache.invalidate("skills")
        self.addCleanup(skill_cache.invalidate, "skills")
        self.addCleanup(clear_commands_cache)

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

        self.assertTrue(provider.chat.called, "engine must invoke provider.chat")
        system_arg = provider.chat.call_args.kwargs.get("system")
        self.assertIsInstance(
            system_arg, list,
            f"Expected block-list shape, got {type(system_arg).__name__}",
        )
        skill_blocks = [
            b for b in system_arg
            if isinstance(b.get("text"), str) and "# Available Skills" in b["text"]
        ]
        self.assertEqual(
            len(skill_blocks), 1,
            "engine must emit exactly one '# Available Skills' block when a "
            "project skill is discoverable — skills=get_skill_tool_commands(cwd) "
            "must be threaded through _build_system_prompt_parts",
        )
        listing = skill_blocks[0]["text"]
        self.assertIn(
            "wiringdemo", listing,
            "the fixture skill name must appear in the model-facing listing",
        )
        self.assertIn(
            "a wiring demo skill", listing,
            "the fixture skill description must appear in the listing",
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

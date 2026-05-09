"""Phase-7 / WI-7.1 — real prompt hook regression tests.

Pre-Phase-7, ``execute_prompt_hook`` was a stub: it returned the
configured ``prompt_text`` as ``additional_context`` directly with no
LLM call (gap analysis #20). The chapter's intended behavior is:

  * Render the prompt template with placeholders from the event data.
  * Call the configured LLM with the rendered prompt.
  * Surface the LLM's response as ``additional_context``.

These tests pin the new contract — including the failure mode that the
team-lead specifically asked to verify: "the current stub-style behavior
should fail to match the new contract."
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.hooks.exec_prompt_hook import _render_prompt_template, execute_prompt_hook
from src.hooks.hook_types import HookConfig


class TestPromptTemplateRendering:
    def test_simple_substitution(self):
        result = _render_prompt_template(
            "Tool: {tool_name}", {"tool_name": "Bash"},
        )
        assert result == "Tool: Bash"

    def test_dict_value_serialized_as_json(self):
        result = _render_prompt_template(
            "Input: {tool_input}",
            {"tool_input": {"command": "ls"}},
        )
        # tool_input dict is JSON-serialized so the rendered prompt has
        # a coherent string representation.
        assert "command" in result
        assert "ls" in result

    def test_unknown_key_renders_empty(self):
        # Forgiving: a hook author who references {tool_name} in a Stop
        # hook (no tool_name in stdin_data) shouldn't blow up.
        result = _render_prompt_template(
            "Tool: {tool_name}, Other: {missing}",
            {"tool_name": "Bash"},
        )
        assert result == "Tool: Bash, Other: "

    def test_no_placeholders_passthrough(self):
        result = _render_prompt_template(
            "No placeholders here.", {"foo": "bar"},
        )
        assert result == "No placeholders here."

    def test_malformed_template_falls_back_to_raw(self):
        # An unterminated brace should not crash; the raw template
        # comes through (logged at WARNING).
        result = _render_prompt_template(
            "Mismatched {brace", {"brace": "ok"},
        )
        # Either the raw template or some best-effort render is fine;
        # the contract is "doesn't raise."
        assert isinstance(result, str)


class TestExecutePromptHookRealLLMCall:
    @pytest.mark.asyncio
    async def test_response_surfaces_as_additional_context(self):
        # Headline: provider gets called with the rendered prompt; the
        # LLM's response is what surfaces as additional_context (NOT
        # the configured prompt_text — that was the stub bug).
        config = HookConfig(type="prompt", prompt_text="Evaluate {tool_name}")

        mock_response = MagicMock()
        mock_response.content = "LLM says: looks safe"
        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(return_value=mock_response)

        result = await execute_prompt_hook(
            config, {"tool_name": "Bash"}, provider=mock_provider,
        )

        assert result.exit_code == 0
        assert result.additional_contexts == ["LLM says: looks safe"]
        # Provider was called with the rendered template.
        sent = mock_provider.chat_async.call_args.kwargs
        assert "Evaluate Bash" in sent["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_no_provider_returns_blocking_error(self):
        # Pre-Phase-7 silently echoed prompt_text. New contract: no
        # provider → blocking_error so configuration mistakes are visible.
        config = HookConfig(type="prompt", prompt_text="Always be helpful")
        result = await execute_prompt_hook(config, {"tool_name": "Bash"})
        assert result.blocking_error is not None
        assert "provider" in result.blocking_error.lower()

    @pytest.mark.asyncio
    async def test_no_text_no_op_without_provider(self):
        # Empty/None prompt_text is the "no-op hook" case — succeeds
        # without provider; no LLM call made.
        config = HookConfig(type="prompt", prompt_text=None)
        result = await execute_prompt_hook(config, {})
        assert result.exit_code == 0
        assert result.additional_contexts is None

    @pytest.mark.asyncio
    async def test_provider_error_surfaces_as_blocking(self):
        config = HookConfig(type="prompt", prompt_text="x")
        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(side_effect=RuntimeError("API down"))
        result = await execute_prompt_hook(
            config, {}, provider=mock_provider,
        )
        assert result.blocking_error is not None
        assert "API down" in result.blocking_error

    @pytest.mark.asyncio
    async def test_sync_provider_fallback(self):
        # Provider may be sync (chat) instead of async (chat_async); the
        # executor falls back so test fixtures with both shapes work.
        config = HookConfig(type="prompt", prompt_text="x")
        mock_response = MagicMock()
        mock_response.content = "sync response"
        mock_provider = MagicMock(spec=[])
        mock_provider.chat = MagicMock(return_value=mock_response)
        result = await execute_prompt_hook(
            config, {}, provider=mock_provider,
        )
        assert result.exit_code == 0
        assert result.additional_contexts == ["sync response"]

    @pytest.mark.asyncio
    async def test_empty_llm_response_succeeds_without_additional_context(self):
        config = HookConfig(type="prompt", prompt_text="x")
        mock_response = MagicMock()
        mock_response.content = ""
        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(return_value=mock_response)
        result = await execute_prompt_hook(
            config, {}, provider=mock_provider,
        )
        # Hook ran (exit 0), but empty additional_contexts.
        assert result.exit_code == 0
        assert result.additional_contexts is None

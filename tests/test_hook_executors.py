import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.hooks.hook_types import HookConfig, HookResult
from src.hooks.exec_agent_hook import execute_agent_hook
from src.hooks.exec_http_hook import execute_http_hook
from src.hooks.exec_prompt_hook import execute_prompt_hook
from src.hooks.post_sampling_hooks import run_post_sampling_hooks
from src.hooks.lifecycle_routers import (
    run_session_start_hooks,
    run_session_end_hooks,
    run_compact_hooks,
)
from src.hooks.registry import AsyncHookRegistry
from src.hooks.hook_types import HookSource


class TestPromptHookExecutor:
    """Phase-7 / WI-7.1 contract: prompt hooks make a real LLM call.

    Pre-Phase-7 stub returned ``prompt_text`` directly as
    ``additional_context``. New contract: render template + call LLM +
    return response. Provider required.
    """

    @pytest.mark.asyncio
    async def test_execute_prompt_hook_with_text_and_provider(self):
        # New contract: provider gets called; response surfaces as
        # additional_context (not the raw template).
        config = HookConfig(type="prompt", prompt_text="Evaluate {tool_name}")

        mock_response = MagicMock()
        mock_response.content = "LLM response: looks fine"
        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(return_value=mock_response)

        result = await execute_prompt_hook(
            config, {"tool_name": "Bash"}, provider=mock_provider,
        )
        assert result.exit_code == 0
        # The LLM response is what surfaces, not the template.
        assert result.additional_contexts == ["LLM response: looks fine"]
        # The template was rendered with tool_name substituted.
        sent_messages = mock_provider.chat_async.call_args.kwargs["messages"]
        assert "Evaluate Bash" in sent_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_execute_prompt_hook_with_text_no_provider_blocks(self):
        # Pre-Phase-7 silently echoed prompt_text; new contract makes
        # the configuration mistake visible.
        config = HookConfig(type="prompt", prompt_text="Always be helpful")
        result = await execute_prompt_hook(config, {"tool_name": "Bash"})
        assert result.blocking_error is not None
        assert "provider" in result.blocking_error.lower()

    @pytest.mark.asyncio
    async def test_execute_prompt_hook_no_text(self):
        # Empty prompt_text is the "hook author registered a no-op"
        # case — still succeeds without provider, no LLM call made.
        config = HookConfig(type="prompt", prompt_text=None)
        result = await execute_prompt_hook(config, {})
        assert result.exit_code == 0
        assert result.additional_contexts is None

    @pytest.mark.asyncio
    async def test_execute_prompt_hook_empty_text(self):
        config = HookConfig(type="prompt", prompt_text="")
        result = await execute_prompt_hook(config, {})
        assert result.exit_code == 0


class TestAgentHookExecutor:
    @pytest.mark.asyncio
    async def test_no_instructions(self):
        config = HookConfig(type="agent", agent_instructions=None)
        result = await execute_agent_hook(config, {})
        assert result.blocking_error is not None
        assert "no instructions" in result.blocking_error.lower()

    @pytest.mark.asyncio
    async def test_no_provider(self):
        config = HookConfig(type="agent", agent_instructions="Check if safe")
        result = await execute_agent_hook(config, {}, provider=None)
        assert result.blocking_error is not None
        assert "provider" in result.blocking_error.lower()

    @pytest.mark.asyncio
    async def test_successful_allow(self):
        config = HookConfig(type="agent", agent_instructions="Check if safe")

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "decision": "allow",
            "reason": "Looks safe",
        })

        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(return_value=mock_response)

        result = await execute_agent_hook(config, {"tool_name": "Bash"}, provider=mock_provider)
        assert result.exit_code == 0
        assert result.permission_behavior == "allow"
        assert result.hook_permission_decision_reason == "Looks safe"

    @pytest.mark.asyncio
    async def test_successful_deny(self):
        config = HookConfig(type="agent", agent_instructions="Deny dangerous commands")

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "decision": "deny",
            "reason": "Too dangerous",
        })

        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(return_value=mock_response)

        result = await execute_agent_hook(config, {"tool_name": "Bash"}, provider=mock_provider)
        assert result.permission_behavior == "deny"

    @pytest.mark.asyncio
    async def test_json_in_text(self):
        config = HookConfig(type="agent", agent_instructions="Check")

        mock_response = MagicMock()
        mock_response.content = 'Here is my evaluation:\n{"decision": "allow", "reason": "OK"}\nDone.'

        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(return_value=mock_response)

        result = await execute_agent_hook(config, {}, provider=mock_provider)
        assert result.permission_behavior == "allow"

    @pytest.mark.asyncio
    async def test_provider_error(self):
        config = HookConfig(type="agent", agent_instructions="Check")

        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(side_effect=Exception("API error"))

        result = await execute_agent_hook(config, {}, provider=mock_provider)
        assert result.blocking_error is not None

    @pytest.mark.asyncio
    async def test_sync_provider_fallback(self):
        config = HookConfig(type="agent", agent_instructions="Check")

        mock_response = MagicMock()
        mock_response.content = json.dumps({"decision": "allow", "reason": "OK"})

        mock_provider = MagicMock(spec=[])
        mock_provider.chat = MagicMock(return_value=mock_response)

        result = await execute_agent_hook(config, {}, provider=mock_provider)
        assert result.exit_code == 0


class TestHttpHookExecutor:
    @pytest.mark.asyncio
    async def test_no_url(self):
        config = HookConfig(type="http", url=None)
        result = await execute_http_hook(config, {})
        assert result.blocking_error is not None

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        config = HookConfig(type="http", url="http://localhost:8080/hook")
        result = await execute_http_hook(config, {})
        assert result.blocking_error is not None
        assert "SSRF" in result.blocking_error

    @pytest.mark.asyncio
    async def test_ssrf_private_ip(self):
        config = HookConfig(type="http", url="http://192.168.1.1/hook")
        result = await execute_http_hook(config, {})
        assert result.blocking_error is not None
        assert "SSRF" in result.blocking_error

    @pytest.mark.asyncio
    async def test_ssrf_metadata(self):
        config = HookConfig(type="http", url="http://169.254.169.254/latest/meta-data/")
        result = await execute_http_hook(config, {})
        assert result.blocking_error is not None

    @pytest.mark.asyncio
    async def test_successful_response(self):
        # Phase-7 / WI-7.3: http hooks now use httpx with a guarded
        # transport. We mock the transport at the get_guarded_client
        # level so the test doesn't make a real network call.
        config = HookConfig(type="http", url="https://hooks.example.com/pre-tool")

        mock_response = MagicMock()
        mock_response.text = json.dumps({"decision": "allow", "reason": "Approved"})
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.hooks.exec_http_hook.get_guarded_client", return_value=mock_client):
            with patch("src.hooks.exec_http_hook.validate_hook_url", return_value=(True, None)):
                result = await execute_http_hook(config, {"tool_name": "Bash"})

        assert result.exit_code == 0
        assert result.permission_behavior == "allow"

    @pytest.mark.asyncio
    async def test_error_response(self):
        config = HookConfig(type="http", url="https://hooks.example.com/pre-tool")

        mock_response = MagicMock()
        mock_response.text = "Internal Server Error"
        mock_response.status_code = 500

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.hooks.exec_http_hook.get_guarded_client", return_value=mock_client):
            with patch("src.hooks.exec_http_hook.validate_hook_url", return_value=(True, None)):
                result = await execute_http_hook(config, {"tool_name": "Bash"})

        assert result.blocking_error is not None
        assert "500" in result.blocking_error


class TestSessionHooks:
    @pytest.mark.asyncio
    async def test_session_start_no_hooks(self):
        registry = AsyncHookRegistry()
        results = await run_session_start_hooks(registry, session_id="test-1")
        assert results == []

    @pytest.mark.asyncio
    async def test_session_end_no_hooks(self):
        registry = AsyncHookRegistry()
        results = await run_session_end_hooks(registry, session_id="test-1")
        assert results == []

    @pytest.mark.asyncio
    async def test_compact_hooks_no_hooks(self):
        registry = AsyncHookRegistry()
        results = await run_compact_hooks(registry, session_id="test-1")
        assert results == []


class TestPostSamplingHooks:
    @pytest.mark.asyncio
    async def test_no_hooks(self):
        registry = AsyncHookRegistry()
        results = await run_post_sampling_hooks(registry, model="claude-sonnet-4-20250514")
        assert results == []

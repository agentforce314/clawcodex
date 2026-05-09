"""Phase-8 / WI-8.3 — ApiQueryHookHelper tests.

Wraps API queries with ``UserPromptSubmit`` (pre) and ``PostSampling``
(post) hook events. Subscribers can:
  * Inject context refresh via ``additional_contexts`` on the
    UserPromptSubmit aggregated decision.
  * Deny the query via either event.
  * Observe the response post-hoc via PostSampling.

Tests cover the helper composition with the real
``_run_hooks_for_event`` (using configured snapshot hooks).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.hooks.api_query_helper import ApiQueryHookDenied, ApiQueryHookHelper
from src.hooks.config_manager import HookConfigManager, HookConfigSnapshot
from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.registry import AsyncHookRegistry


@dataclass
class _MockOptions:
    hooks: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


@dataclass
class _MockContext:
    options: _MockOptions = field(default_factory=_MockOptions)
    hook_config_manager: Any | None = None
    workspace_trusted: bool = True
    abort_controller: Any | None = None
    session_hook_registry: Any | None = None
    session_id: str | None = None
    workspace_root: Path | None = None
    provider: Any | None = None
    model: str | None = None


def _manager_with(hooks: dict[str, list[HookConfig]]) -> HookConfigManager:
    m = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    m._snapshot = HookConfigSnapshot(hooks=hooks, timestamp=0.0, source_path=None)
    return m


class TestApiQueryHelperBasics:
    @pytest.mark.asyncio
    async def test_query_runs_when_no_hooks_configured(self):
        # No hooks → both events fire with empty entries → query runs
        # transparently. Helper is a thin pass-through in this case.
        ctx = _MockContext(hook_config_manager=_manager_with({}))
        helper = ApiQueryHookHelper(tool_use_context=ctx)

        query_fn = AsyncMock(return_value="response-text")
        result = await helper.run(
            query_fn=query_fn, prompt="hello",
        )
        assert result == "response-text"
        query_fn.assert_called_once()
        # Effective prompt was unchanged (no additional_contexts to add).
        sent_prompt = query_fn.call_args.kwargs["prompt"]
        assert sent_prompt == "hello"

    @pytest.mark.asyncio
    async def test_query_passes_through_kwargs(self):
        ctx = _MockContext(hook_config_manager=_manager_with({}))
        helper = ApiQueryHookHelper(tool_use_context=ctx)

        query_fn = AsyncMock(return_value="ok")
        await helper.run(
            query_fn=query_fn, prompt="x",
            model="claude-sonnet-4", max_tokens=512,
        )
        sent = query_fn.call_args.kwargs
        assert sent["model"] == "claude-sonnet-4"
        assert sent["max_tokens"] == 512


class TestApiQueryHelperContextInjection:
    @pytest.mark.asyncio
    async def test_pre_hook_additional_context_appended_to_prompt(self):
        # A UserPromptSubmit hook that emits additional_contexts has its
        # output concatenated to the prompt before the query runs.
        # We use a command hook that emits structured JSON via stdout.
        pre_hook = HookConfig(
            type="command",
            command='echo \'{"additionalContexts": ["[git status: clean]"]}\'',
            source=HookSource.USER_SETTINGS,
        )
        ctx = _MockContext(
            hook_config_manager=_manager_with({"UserPromptSubmit": [pre_hook]}),
        )
        helper = ApiQueryHookHelper(tool_use_context=ctx)

        query_fn = AsyncMock(return_value="response")
        await helper.run(query_fn=query_fn, prompt="What's the status?")

        # Effective prompt has the additional_context appended.
        sent_prompt = query_fn.call_args.kwargs["prompt"]
        assert "What's the status?" in sent_prompt
        assert "[git status: clean]" in sent_prompt


class TestApiQueryHelperDenyPath:
    @pytest.mark.asyncio
    async def test_pre_hook_deny_aborts_query(self):
        # UserPromptSubmit hook returns deny → query never runs;
        # ApiQueryHookDenied raised.
        pre_deny = HookConfig(
            type="command",
            command='echo \'{"decision": "deny", "reason": "rate limited"}\'',
            source=HookSource.USER_SETTINGS,
        )
        ctx = _MockContext(
            hook_config_manager=_manager_with({"UserPromptSubmit": [pre_deny]}),
        )
        helper = ApiQueryHookHelper(tool_use_context=ctx)

        query_fn = AsyncMock(return_value="should-not-run")
        with pytest.raises(ApiQueryHookDenied, match="rate limited"):
            await helper.run(query_fn=query_fn, prompt="x")

        # Query was NOT called.
        query_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_post_hook_deny_after_query_raises(self):
        # PostSampling hook denies the response → query DID run, but
        # the helper raises so the caller knows the response was rejected.
        post_deny = HookConfig(
            type="command",
            command='echo \'{"decision": "deny", "reason": "policy block"}\'',
            source=HookSource.USER_SETTINGS,
        )
        ctx = _MockContext(
            hook_config_manager=_manager_with({"PostSampling": [post_deny]}),
        )
        helper = ApiQueryHookHelper(tool_use_context=ctx)

        query_fn = AsyncMock(return_value="raw-response")
        with pytest.raises(ApiQueryHookDenied, match="policy block"):
            await helper.run(query_fn=query_fn, prompt="x")

        # Query DID run before the post-deny.
        query_fn.assert_called_once()

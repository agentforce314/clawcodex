from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Callable
from uuid import uuid4

from ..types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
)
from ..tool_system.build_tool import Tools
from ..tool_system.context import ToolContext
from ..tool_system.registry import ToolRegistry
from ..utils.abort_controller import AbortController, create_abort_controller
from ..providers.base import BaseProvider
from ..context_system import build_context_prompt
from ..context_system.prompt_assembly import (
    append_system_context,
    build_full_system_prompt,
    fetch_system_prompt_parts,
    prepend_user_context,
)

from .query import QueryParams, StreamEvent, query
from ..services.compact.pipeline import PipelineConfig


@dataclass
class QueryEngineConfig:
    cwd: Path
    provider: BaseProvider
    tool_registry: ToolRegistry
    tools: Tools
    tool_context: ToolContext
    abort_controller: AbortController | None = None
    system_prompt: str | None = None
    custom_system_prompt: str | None = None
    append_system_prompt: str | None = None
    max_turns: int | None = None
    initial_messages: list[Message] | None = None
    query_source: str = "repl_main_thread"
    user_context: dict[str, str] | None = None
    system_context: dict[str, str] | None = None


class QueryEngine:
    def __init__(self, config: QueryEngineConfig) -> None:
        self._config = config
        self._mutable_messages: list[Message] = list(config.initial_messages or [])
        self._abort_controller = config.abort_controller or create_abort_controller()
        self._total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._session_id: str = uuid4().hex

    @property
    def mutable_messages(self) -> list[Message]:
        return self._mutable_messages

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def total_usage(self) -> dict[str, int]:
        return dict(self._total_usage)

    async def _build_system_prompt_parts(
        self,
    ) -> tuple[str, dict[str, str], dict[str, str]]:
        """
        Build system prompt with user/system context.

        Returns (system_prompt_str, user_context, system_context).
        Uses build_full_system_prompt() to produce identity, tool docs,
        environment, and tool usage instructions — then appends user/system
        context from fetch_system_prompt_parts().
        """
        # If full system prompt was provided directly, use it
        if self._config.system_prompt:
            user_ctx = self._config.user_context or {}
            sys_ctx = self._config.system_context or {}
            return self._config.system_prompt, user_ctx, sys_ctx

        # Use WS-5 context assembly
        try:
            cwd = str(self._config.tool_context.cwd or self._config.cwd)
            parts = await fetch_system_prompt_parts(
                cwd=cwd,
                custom_system_prompt=self._config.custom_system_prompt,
            )

            if self._config.custom_system_prompt:
                # Custom prompt: use it directly with optional append
                prompt_sections = [self._config.custom_system_prompt]
                if self._config.append_system_prompt:
                    prompt_sections.append(self._config.append_system_prompt)
            else:
                # Build full system prompt with TS-matching 7 modules + env.
                # Per-tool prompts are NOT in the system prompt — they're sent
                # via the API tools parameter (tool.prompt() → description).
                full_prompt = build_full_system_prompt(
                    cwd=cwd,
                    append_system_prompt=self._config.append_system_prompt,
                )
                prompt_sections = [full_prompt] if full_prompt else parts.default_system_prompt
                if not full_prompt and self._config.append_system_prompt:
                    prompt_sections.append(self._config.append_system_prompt)

            system_prompt = append_system_context(
                prompt_sections, parts.system_context,
            )
            return system_prompt, parts.user_context, parts.system_context

        except Exception:
            # Fallback to legacy builder
            try:
                context_prompt = build_context_prompt(
                    self._config.cwd,
                    cwd=self._config.tool_context.cwd,
                )
            except Exception:
                context_prompt = ""
            return context_prompt, {}, {}

    async def submit_message(
        self,
        prompt: str,
        *,
        on_message: Callable[[Message | StreamEvent], None] | None = None,
    ) -> AsyncGenerator[Message | StreamEvent, None]:
        user_msg = UserMessage(content=prompt)
        self._mutable_messages.append(user_msg)

        system_prompt, user_context, system_context = (
            await self._build_system_prompt_parts()
        )

        # Prepend user context (CLAUDE.md + date) as <system-reminder>
        messages_for_query = prepend_user_context(
            list(self._mutable_messages), user_context,
        )

        # TS query loop runs 5-layer compression pipeline every iteration
        # (Phase 0: toolResultBudget → snip → microcompact → collapse → autocompact).
        # Enable it by passing a PipelineConfig.
        #
        # Build read_file_state from the tool context's read_file_fingerprints
        # so post-compact attachments can re-inject recently read files.
        # The attachment builder only reads timestamp from each entry and
        # re-reads content from disk, so we just need the timestamp.
        read_file_state: dict[str, Any] = {}
        try:
            for path, fp in self._config.tool_context.read_file_fingerprints.items():
                # fp is (mtime, size) or (mtime, size, partial)
                read_file_state[str(path)] = {"timestamp": fp[0]}
        except Exception:
            pass

        pipeline_config = PipelineConfig(
            provider=self._config.provider,
            model=getattr(self._config.provider, 'model', '') or '',
            read_file_state=read_file_state or None,
        )

        params = QueryParams(
            messages=messages_for_query,
            system_prompt=system_prompt,
            tools=self._config.tools,
            tool_registry=self._config.tool_registry,
            tool_use_context=self._config.tool_context,
            provider=self._config.provider,
            abort_controller=self._abort_controller,
            query_source=self._config.query_source,
            max_turns=self._config.max_turns,
            user_context=user_context,
            system_context=system_context,
            pipeline_config=pipeline_config,
        )

        async for message in query(params):
            if isinstance(message, StreamEvent):
                if on_message:
                    on_message(message)
                yield message
                continue

            if isinstance(message, SystemMessage):
                if on_message:
                    on_message(message)
                yield message
                continue

            self._mutable_messages.append(message)

            if on_message:
                on_message(message)

            yield message

    def interrupt(self) -> None:
        self._abort_controller.abort("user_interrupt")

    def get_messages(self) -> list[Message]:
        return list(self._mutable_messages)

    def get_session_id(self) -> str:
        return self._session_id

    def reset_abort_controller(self) -> None:
        self._abort_controller = create_abort_controller()

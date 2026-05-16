from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Callable
from uuid import uuid4

from ..types.content_blocks import ContentBlock
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
    append_system_context_blocks,
    build_full_system_prompt,
    build_full_system_prompt_blocks,
    fetch_system_prompt_parts,
    prepend_user_context,
)

from .query import QueryParams, StreamEvent, query
from ..services.compact.pipeline import PipelineConfig
from ..services.compact.autocompact import AutoCompactTracking


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
    # WI-2.3 (critic M1): MCP servers loaded for this session. Threaded into
    # build_full_system_prompt_blocks so the global-scope gate at
    # cache_state.should_use_global_cache_scope can disable scope='global'
    # when MCP schemas are present (per chapter line 91, MCP schemas are
    # per-user and must NOT land in the cross-user global cache tier).
    mcp_servers: list[Any] | None = None


class QueryEngine:
    def __init__(self, config: QueryEngineConfig) -> None:
        self._config = config
        self._mutable_messages: list[Message] = list(config.initial_messages or [])
        self._abort_controller = config.abort_controller or create_abort_controller()
        # Mirror the controller onto the tool context. The query loop already
        # reads ``params.abort_controller`` at turn boundaries, but the
        # streaming tool executor, Bash supervisor, tool hooks and — most
        # importantly — Agent subagents read ``context.abort_controller``
        # to learn about ESC. Leaving the field ``None`` lets long-running
        # tools (especially subagent dispatches) ignore the user's
        # interrupt until they finish naturally.
        self._config.tool_context.abort_controller = self._abort_controller
        self._total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._session_id: str = uuid4().hex
        # Ch5/B.5 prereq — the autocompact circuit-breaker counter must
        # survive across submit_message calls so 3 consecutive failures
        # actually trip the breaker. A fresh PipelineConfig per submit
        # would reset the counter every prompt. Hold the SAME tracking
        # instance on the engine and reuse it.
        self._auto_compact_tracking: AutoCompactTracking = AutoCompactTracking()

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
    ) -> tuple[str | list[dict[str, Any]], dict[str, str], dict[str, str]]:
        """
        Build system prompt with user/system context.

        Returns (system_prompt, user_context, system_context).

        ``system_prompt`` is ``list[dict[str, Any]]`` for the production
        cold-start path (no caller-provided system prompt or custom prompt) —
        each section becomes a block, with ``cache_control: ephemeral``
        markers placed at the GLOBAL/SESSION/REQUEST scope boundaries so the
        Anthropic API engages prompt caching. Mirrors TS ``getSystemPrompt()``
        return shape used at ``services/api/claude.ts``.

        ``system_prompt`` is ``str`` only when:
          - The caller provided ``system_prompt`` directly (SDK opt-out path).
          - The caller provided ``custom_system_prompt`` (single-block override).
          - The fallback to ``build_context_prompt`` is taken (Exception path).

        Both shapes are accepted by ``QueryParams.system_prompt`` and forward
        cleanly through to ``client.messages.create(system=...)`` via the
        Anthropic SDK's ``Union[str, Iterable[TextBlockParam]]`` type.
        """
        # Caller-provided system prompt → use as-is. Could be str or blocks
        # (the type is permissive at the config layer).
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
                # Custom prompt: single-block override. The cache_control
                # plumbing doesn't apply — SDK callers using a custom prompt
                # opt out of the section taxonomy. Return list-shape with one
                # block so downstream typing is uniform.
                blocks: list[dict[str, Any]] = [
                    {"type": "text", "text": self._config.custom_system_prompt}
                ]
                if self._config.append_system_prompt:
                    blocks.append(
                        {"type": "text", "text": self._config.append_system_prompt}
                    )
                # Append git-status etc. as a final uncached block.
                system_prompt = append_system_context_blocks(blocks, parts.system_context)
                return system_prompt, parts.user_context, parts.system_context

            # Production cold-start path: assemble the full block list with
            # cache_control markers at scope boundaries. Per WI-1.1, the
            # final API request shape is::
            #
            #   [global blocks…, ⟨ephemeral⟩,
            #    __SYSTEM_PROMPT_DYNAMIC_BOUNDARY__,
            #    session blocks…, ⟨ephemeral⟩,
            #    request blocks…, ⟨ephemeral⟩,
            #    git-status block (uncached)]
            blocks = build_full_system_prompt_blocks(
                cwd=cwd,
                append_system_prompt=self._config.append_system_prompt,
                # WI-2.2: thread query_source so the cache_control marker
                # picks 5m vs 1h based on the per-call decision in
                # ``cache_state.should_1h_cache_ttl``.
                query_source=self._config.query_source,
                # WI-2.3: thread provider AND mcp_servers so GLOBAL-tier
                # blocks emit ``scope: 'global'`` only when ALL hold:
                # firstParty + no-MCP + opt-in env. Critic M1: omitting
                # mcp_servers here would bypass the MCP gate at the
                # integration layer (per-user MCP schemas would land in
                # the cross-user GLOBAL cache, violating the chapter's
                # privacy guarantee at line 91).
                provider=self._config.provider,
                mcp_servers=self._config.mcp_servers,
            )
            system_prompt = append_system_context_blocks(
                blocks, parts.system_context,
            )
            return system_prompt, parts.user_context, parts.system_context

        except Exception:
            # Fallback to legacy str-shape builder. This branch is only hit
            # on assembly errors; in steady state the production path above
            # always returns the block-list shape.
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
        prompt: str | list[ContentBlock],
        *,
        on_message: Callable[[Message | StreamEvent], None] | None = None,
    ) -> AsyncGenerator[Message | StreamEvent, None]:
        # ``MessageContent = str | list[ContentBlock]`` already supports
        # both shapes; the list form lets callers attach image/document
        # content blocks alongside the text prompt (e.g. from @image.png
        # @-mentions in the REPL).
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
            # Ch5/B.5 — thread the session-scoped tracking instance so
            # the autocompact circuit-breaker can count consecutive
            # failures across user prompts. ``auto_compact_if_needed``
            # mutates ``tracking.consecutive_failures`` in place.
            autocompact_tracking=self._auto_compact_tracking,
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
                # Contract: on_message callbacks must NOT mirror engine
                # state (read get_messages() / mutable_messages) — the
                # image-unsupported strip below runs after on_message,
                # so a mirror taken here may see un-stripped state for
                # one frame. Production callers don't mirror here today
                # (only legacy run_agent.py uses on_message at all, and
                # it consumes content directly), so this is a contract
                # note for future callers rather than a current defect.
                on_message(message)

            # Image-unsupported recovery: the provider rejected the
            # request because the model has zero image capability. The
            # user's image-bearing message is now in _mutable_messages
            # and would re-trigger the same 404 on every subsequent
            # submit_message() call. Strip image blocks (keeping the
            # text intent) so text-only follow-ups work. The TypeScript
            # reference expects the user to manually rewind via the Ink
            # MessageSelector ("Double press esc to go back"); the Rich
            # REPL has no equivalent UI, so we recover automatically
            # here. Mirrored by repl/core.py for session.conversation.
            if (
                isinstance(message, AssistantMessage)
                and getattr(message, "_api_error", None) == "image_unsupported"
            ):
                from ..context_system.microcompact import (
                    strip_images_from_typed_messages,
                )
                self._mutable_messages = strip_images_from_typed_messages(
                    self._mutable_messages
                )

            yield message

    def interrupt(self) -> None:
        self._abort_controller.abort("user_interrupt")

    def get_messages(self) -> list[Message]:
        return list(self._mutable_messages)

    def get_session_id(self) -> str:
        return self._session_id

    def reset_abort_controller(self) -> None:
        self._abort_controller = create_abort_controller()
        # Keep the tool context's controller in sync with the engine's —
        # otherwise the next turn's subagents and Bash commands would still
        # be carrying the previous (possibly aborted) controller.
        self._config.tool_context.abort_controller = self._abort_controller

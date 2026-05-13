"""StreamingToolExecutor — mirrors TypeScript StreamingToolExecutor.ts.

Executes tools as they stream in with concurrency control:
- Concurrent-safe tools can execute in parallel with other concurrent-safe tools
- Non-concurrent tools must execute alone (exclusive access)
- Results are buffered and emitted in the order tools were received
- Sibling abort: Bash errors cascade to cancel sibling tools
- Three-tier AbortController hierarchy: parent → sibling → per-tool
- Progress wake-up mechanism via asyncio.Event
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncGenerator, Generator, Literal

from src.types.messages import (
    AssistantMessage,
    Message,
    create_user_message,
)
from src.utils.abort_controller import (
    AbortController,
    AbortError,
    create_child_abort_controller,
)

if TYPE_CHECKING:
    from src.tool_system.build_tool import Tool, Tools
    from src.tool_system.context import ToolContext

logger = logging.getLogger(__name__)

BASH_TOOL_NAME = "Bash"

ToolStatus = Literal["queued", "executing", "completed", "yielded"]


@dataclass
class MessageUpdate:
    message: Message | None = None
    new_context: ToolContext | None = None


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class TrackedTool:
    id: str
    block: ToolUseBlock
    assistant_message: AssistantMessage
    status: ToolStatus
    is_concurrency_safe: bool
    pending_progress: list[Message] = field(default_factory=list)
    promise: asyncio.Task[None] | None = None
    results: list[Message] | None = None
    context_modifiers: list[Any] | None = None


class StreamingToolExecutor:
    def __init__(
        self,
        tool_definitions: Tools,
        can_use_tool: Any,
        tool_use_context: ToolContext,
    ) -> None:
        self._tools: list[TrackedTool] = []
        self._tool_definitions = tool_definitions
        self._can_use_tool = can_use_tool
        self._tool_use_context = tool_use_context
        self._has_errored = False
        self._errored_tool_description = ""
        self._sibling_abort_controller = create_child_abort_controller(
            tool_use_context.abort_controller or AbortController()
        )
        self._discarded = False
        self._progress_available_event = asyncio.Event()
        # Retain ensure_future tasks until they complete; asyncio doesn't
        # hold strong references to scheduled coroutines and may GC them
        # mid-flight (RuntimeWarning: coroutine was never awaited).
        self._pending_queue_tasks: set[asyncio.Task[None]] = set()

    def discard(self) -> None:
        self._discarded = True

    def add_tool(self, block: ToolUseBlock, assistant_message: AssistantMessage) -> None:
        from src.tool_system.build_tool import find_tool_by_name
        # Local import to avoid an orchestrator → streaming_executor cycle at
        # module load (orchestrator already imports from this module).
        from src.services.tool_execution.orchestrator import (
            classify_concurrency_safe,
        )

        tool_definition = find_tool_by_name(self._tool_definitions, block.name)
        if tool_definition is None:
            self._tools.append(TrackedTool(
                id=block.id,
                block=block,
                assistant_message=assistant_message,
                status="completed",
                is_concurrency_safe=True,
                results=[create_user_message(
                    content=[{
                        "type": "tool_result",
                        "content": f"<tool_use_error>Error: No such tool available: {block.name}</tool_use_error>",
                        "is_error": True,
                        "tool_use_id": block.id,
                    }],
                    toolUseResult=f"Error: No such tool available: {block.name}",
                )],
            ))
            return

        is_concurrency_safe = classify_concurrency_safe(
            tool_definition, block.input,
        )

        self._tools.append(TrackedTool(
            id=block.id,
            block=block,
            assistant_message=assistant_message,
            status="queued",
            is_concurrency_safe=is_concurrency_safe,
        ))

        # Retain the queue task; otherwise asyncio may GC it
        # (RuntimeWarning: coroutine was never awaited). The done
        # callback discards it once the queue tick finishes.
        task = asyncio.ensure_future(self._process_queue())
        self._pending_queue_tasks.add(task)
        task.add_done_callback(self._pending_queue_tasks.discard)

    def _can_execute_tool(self, is_concurrency_safe: bool) -> bool:
        executing = [t for t in self._tools if t.status == "executing"]
        return (
            len(executing) == 0
            or (is_concurrency_safe and all(t.is_concurrency_safe for t in executing))
        )

    async def _process_queue(self) -> None:
        for tool in self._tools:
            if tool.status != "queued":
                continue

            if self._can_execute_tool(tool.is_concurrency_safe):
                await self._execute_tool(tool)
            else:
                if not tool.is_concurrency_safe:
                    break

    def _create_synthetic_error_message(
        self,
        tool_use_id: str,
        reason: Literal["sibling_error", "user_interrupted", "streaming_fallback"],
        assistant_message: AssistantMessage,
    ) -> Message:
        if reason == "user_interrupted":
            from src.types.messages import REJECT_MESSAGE
            return create_user_message(
                content=[{
                    "type": "tool_result",
                    "content": REJECT_MESSAGE,
                    "is_error": True,
                    "tool_use_id": tool_use_id,
                }],
                toolUseResult="User rejected tool use",
            )
        if reason == "streaming_fallback":
            return create_user_message(
                content=[{
                    "type": "tool_result",
                    "content": "<tool_use_error>Error: Streaming fallback - tool execution discarded</tool_use_error>",
                    "is_error": True,
                    "tool_use_id": tool_use_id,
                }],
                toolUseResult="Streaming fallback - tool execution discarded",
            )
        desc = self._errored_tool_description
        msg = (
            f"Cancelled: parallel tool call {desc} errored"
            if desc
            else "Cancelled: parallel tool call errored"
        )
        return create_user_message(
            content=[{
                "type": "tool_result",
                "content": f"<tool_use_error>{msg}</tool_use_error>",
                "is_error": True,
                "tool_use_id": tool_use_id,
            }],
            toolUseResult=msg,
        )

    def _get_abort_reason(
        self, tool: TrackedTool
    ) -> Literal["sibling_error", "user_interrupted", "streaming_fallback"] | None:
        if self._discarded:
            return "streaming_fallback"
        if self._has_errored:
            return "sibling_error"
        ctx_abort = self._tool_use_context.abort_controller
        if ctx_abort and ctx_abort.signal.aborted:
            if ctx_abort.signal.reason == "interrupt":
                behavior = self._get_tool_interrupt_behavior(tool)
                return "user_interrupted" if behavior == "cancel" else None
            return "user_interrupted"
        return None

    def _get_tool_interrupt_behavior(self, tool: TrackedTool) -> Literal["cancel", "block"]:
        from src.tool_system.build_tool import find_tool_by_name

        definition = find_tool_by_name(self._tool_definitions, tool.block.name)
        if definition is None or definition.interrupt_behavior is None:
            return "block"
        try:
            return definition.interrupt_behavior()
        except Exception:
            return "block"

    def _get_tool_description(self, tool: TrackedTool) -> str:
        inp = tool.block.input or {}
        summary = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
        if isinstance(summary, str) and summary:
            truncated = summary[:40] + "\u2026" if len(summary) > 40 else summary
            return f"{tool.block.name}({truncated})"
        return tool.block.name

    def _update_interruptible_state(self) -> None:
        """Publish whether the currently executing tool set supports interrupt.

        Mirrors TS ``updateInterruptibleState`` at
        ``StreamingToolExecutor.ts:254``. Called on every transition into or
        out of ``executing`` status. The flag is ``True`` only when at least
        one tool is executing AND every executing tool's
        ``interrupt_behavior()`` returns ``"cancel"``. An empty executing
        set yields ``False`` -- the UI shouldn't suggest "press ESC" when
        nothing is running. The context's setter is optional; ``None``
        (the SDK/test default) makes this a no-op, matching TS's optional
        chaining call ``setHasInterruptibleToolInProgress?.(...)``.
        """
        setter = self._tool_use_context.set_has_interruptible_tool_in_progress
        if setter is None:
            return
        executing = [t for t in self._tools if t.status == "executing"]
        interruptible = (
            len(executing) > 0
            and all(
                self._get_tool_interrupt_behavior(t) == "cancel"
                for t in executing
            )
        )
        try:
            setter(interruptible)
        except Exception as exc:
            # Setter failures must not poison the executor \u2014 UI bugs
            # shouldn't break tool dispatch. TS's optional-chain call
            # would let an exception propagate, but in practice the only
            # production setter is a ref-assignment that cannot throw.
            # In Python we suppress + log to match the spirit of "the UI
            # cannot abort the tool runtime."
            logger.warning(
                "set_has_interruptible_tool_in_progress raised: %s", exc
            )

    async def _execute_tool(self, tool: TrackedTool) -> None:
        tool.status = "executing"
        if self._tool_use_context.set_in_progress_tool_use_ids:
            self._tool_use_context.set_in_progress_tool_use_ids(
                lambda prev: prev | {tool.id}
            )
        # Site 1 of 3 — mirrors TS StreamingToolExecutor.ts:270.
        # Publish that the executing set grew; the UI may now want to
        # show an "interruptible" indicator (or hide it if this tool's
        # interrupt_behavior is "block").
        self._update_interruptible_state()

        messages: list[Message] = []
        context_modifiers: list[Any] = []

        async def collect_results() -> None:
            initial_abort = self._get_abort_reason(tool)
            if initial_abort:
                messages.append(
                    self._create_synthetic_error_message(
                        tool.id, initial_abort, tool.assistant_message
                    )
                )
                tool.results = messages
                tool.context_modifiers = context_modifiers
                tool.status = "completed"
                # Site 2 of 3 — mirrors TS line 290 (early-return after
                # synthetic error). Executing set shrank by one.
                self._update_interruptible_state()
                return

            tool_abort_controller = create_child_abort_controller(
                self._sibling_abort_controller
            )

            def _on_tool_abort() -> None:
                if (
                    tool_abort_controller.signal.reason != "sibling_error"
                    and self._tool_use_context.abort_controller
                    and not self._tool_use_context.abort_controller.signal.aborted
                    and not self._discarded
                ):
                    self._tool_use_context.abort_controller.abort(
                        tool_abort_controller.signal.reason
                    )

            tool_abort_controller.signal.add_listener(_on_tool_abort, once=True)

            # Per-tool copy of the context with its own abort controller.
            # Mirrors TS `{ ...this.toolUseContext, abortController: ... }`.
            # Mutating self._tool_use_context here would race when sibling
            # tools execute concurrently (each would stomp the other's
            # abort_controller mid-flight).
            tool_context_with_abort = dataclasses.replace(
                self._tool_use_context,
                abort_controller=tool_abort_controller,
            )

            this_tool_errored = False

            try:
                from src.services.tool_execution.tool_execution import run_tool_use

                async for update in run_tool_use(
                    tool.block,
                    tool.assistant_message,
                    self._can_use_tool,
                    tool_context_with_abort,
                ):
                    abort_reason = self._get_abort_reason(tool)
                    if abort_reason and not this_tool_errored:
                        messages.append(
                            self._create_synthetic_error_message(
                                tool.id, abort_reason, tool.assistant_message
                            )
                        )
                        break

                    msg = update.get("message") if isinstance(update, dict) else getattr(update, "message", None)
                    context_mod = update.get("context_modifier") if isinstance(update, dict) else getattr(update, "context_modifier", None)

                    is_error_result = False
                    if msg and hasattr(msg, "type") and msg.type == "user":
                        content = msg.content if hasattr(msg, "content") else None
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                                    is_error_result = True
                                    break

                    if is_error_result:
                        this_tool_errored = True
                        if tool.block.name == BASH_TOOL_NAME:
                            self._has_errored = True
                            self._errored_tool_description = self._get_tool_description(tool)
                            self._sibling_abort_controller.abort("sibling_error")

                    if msg:
                        if hasattr(msg, "type") and msg.type == "progress":
                            tool.pending_progress.append(msg)
                            self._progress_available_event.set()
                        else:
                            messages.append(msg)

                    if context_mod:
                        context_modifiers.append(context_mod)
            except AbortError:
                abort_reason = self._get_abort_reason(tool)
                if abort_reason and not this_tool_errored:
                    messages.append(
                        self._create_synthetic_error_message(
                            tool.id, abort_reason or "user_interrupted", tool.assistant_message
                        )
                    )
            except Exception as e:
                logger.error("Tool execution error: %s", e)
                messages.append(create_user_message(
                    content=[{
                        "type": "tool_result",
                        "content": f"<tool_use_error>Error: {e}</tool_use_error>",
                        "is_error": True,
                        "tool_use_id": tool.id,
                    }],
                    toolUseResult=f"Error: {e}",
                ))

            tool.results = messages
            tool.context_modifiers = context_modifiers
            tool.status = "completed"
            # Site 3 of 3 — mirrors TS line 386 (normal completion path).
            # Executing set shrank by one; UI may want to flip the
            # interruptible indicator off (or back on if a previously
            # blocking tool was the only non-cancellable member).
            self._update_interruptible_state()

            if not tool.is_concurrency_safe and context_modifiers:
                for modifier in context_modifiers:
                    if callable(modifier):
                        self._tool_use_context = modifier(self._tool_use_context)
                    elif hasattr(modifier, "modify_context"):
                        self._tool_use_context = modifier.modify_context(self._tool_use_context)

        task = asyncio.ensure_future(collect_results())
        tool.promise = task

        def _on_done(t: asyncio.Task[None]) -> None:
            asyncio.ensure_future(self._process_queue())

        task.add_done_callback(_on_done)

    def get_completed_results(self) -> Generator[MessageUpdate, None, None]:
        if self._discarded:
            return

        for tool in self._tools:
            while tool.pending_progress:
                progress_msg = tool.pending_progress.pop(0)
                yield MessageUpdate(message=progress_msg, new_context=self._tool_use_context)

            if tool.status == "yielded":
                continue

            if tool.status == "completed" and tool.results is not None:
                tool.status = "yielded"
                for message in tool.results:
                    yield MessageUpdate(message=message, new_context=self._tool_use_context)
                _mark_tool_use_as_complete(self._tool_use_context, tool.id)
            elif tool.status == "executing" and not tool.is_concurrency_safe:
                break

    async def get_remaining_results(self) -> AsyncGenerator[MessageUpdate, None]:
        if self._discarded:
            return

        while self._has_unfinished_tools():
            await self._process_queue()

            for result in self.get_completed_results():
                yield result

            if not self._has_executing_tools():
                continue

            # Idle-wait until progress arrives or any executing tool
            # finishes. Order matters here:
            #   1. clear() the event FIRST.
            #   2. THEN re-check for pending progress / completion.
            #   3. THEN await.
            # The TS Promise-replacement style hides this; the asyncio
            # Event flag is sticky, so checking before clearing leaves a
            # window where a producer's set() between check and clear
            # gets wiped, and the wait blocks until a tool finishes
            # (visible UI lag during long concurrent batches).
            self._progress_available_event.clear()

            if self._has_completed_results() or self._has_pending_progress():
                continue

            executing_promises = [
                t.promise for t in self._tools
                if t.status == "executing" and t.promise is not None
            ]

            if not executing_promises:
                continue

            progress_wait_task = asyncio.ensure_future(
                self._progress_available_event.wait()
            )
            try:
                await asyncio.wait(
                    [*executing_promises, progress_wait_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if not progress_wait_task.done():
                    progress_wait_task.cancel()

        for result in self.get_completed_results():
            yield result

    def _has_pending_progress(self) -> bool:
        return any(len(t.pending_progress) > 0 for t in self._tools)

    def _has_completed_results(self) -> bool:
        return any(t.status == "completed" for t in self._tools)

    def _has_executing_tools(self) -> bool:
        return any(t.status == "executing" for t in self._tools)

    def _has_unfinished_tools(self) -> bool:
        return any(t.status != "yielded" for t in self._tools)

    def get_updated_context(self) -> ToolContext:
        return self._tool_use_context


def _mark_tool_use_as_complete(tool_use_context: ToolContext, tool_use_id: str) -> None:
    if tool_use_context.set_in_progress_tool_use_ids:
        tool_use_context.set_in_progress_tool_use_ids(
            lambda prev: prev - {tool_use_id}
        )

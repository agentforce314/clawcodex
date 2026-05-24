"""Public Python API for running a single query.

Wraps the headless entrypoint for programmatic use.
"""

from __future__ import annotations

import asyncio
import io
import queue
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from ..capabilities.event_protocol import ToolEventProtocol
    from ..capabilities.headless_runner import HeadlessSessionOptions


@dataclass
class QueryConfig:
    """Configuration for a single query run."""

    prompt: str
    workspace: str | Path
    provider: str = "anthropic"
    model: str | None = None
    tools: list[str] | None = None  # tool names to enable; None = all
    permission_mode: str = "dontAsk"
    max_turns: int = 20


@dataclass
class TextDelta:
    """Streaming text chunk."""

    content: str


@dataclass
class ToolCallEvent:
    """Tool call event from the agent."""

    tool_name: str
    params: dict[str, Any]
    tool_use_id: str | None = None
    _approved: bool | None = None
    _deny_reason: str | None = None

    @property
    def is_approved(self) -> bool | None:
        return self._approved


@dataclass
class ToolResultEvent:
    """Tool result event from the agent."""

    tool_name: str
    result: dict[str, Any]


@dataclass
class PhaseComplete:
    """One phase (multiple turns) finished."""

    phase: int
    turn_count: int


@dataclass
class TurnComplete:
    """One turn finished."""

    turn: int


@dataclass
class SessionComplete:
    """Session finished."""

    reason: str


QueryEvent = TextDelta | ToolCallEvent | ToolResultEvent | TurnComplete | PhaseComplete | SessionComplete


class QueryRunner:
    """Execute a single prompt through ClawCodex query engine."""

    def __init__(self, config: QueryConfig) -> None:
        self.config = config

    async def stream(self) -> AsyncIterator[QueryEvent]:
        """Yield query events as they occur.

        Uses the headless runner registry under the hood, which dispatches
        to the configured backend (default: upstream headless entrypoint).
        The caller observes tool events via ``on_event`` without needing
        to import from upstream.
        """
        # Import the headless session runner — this stays off the upstream
        # import path at module-load time; the concrete implementation is
        # loaded lazily inside run_headless_session.
        from ..capabilities.headless_runner import HeadlessSessionOptions, run_headless_session

        event_queue: queue.Queue[Any] = queue.Queue()

        def on_event(tool_event: Any) -> None:
            try:
                event_queue.put(tool_event)
            except Exception:
                pass

        stdout = io.StringIO()
        session_opts = HeadlessSessionOptions(
            prompt=self.config.prompt,
            workspace_root=Path(self.config.workspace),
            provider_name=self.config.provider,
            model=self.config.model,
            max_turns=self.config.max_turns,
            permission_mode=self.config.permission_mode,
            stdout=stdout,
            stderr=stdout,
            on_event=on_event,
        )

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, run_headless_session, session_opts)

        # Drain the event queue while the headless session runs in the background.
        # A short timeout lets us poll for completion without busy-waiting.
        while True:
            try:
                ev: Any = event_queue.get(timeout=0.05)
                # Access via duck-typed attributes (matches ToolEventProtocol)
                kind = getattr(ev, "kind", None)
                tool_name = getattr(ev, "tool_name", "")
                tool_input = getattr(ev, "tool_input", None)
                tool_use_id = getattr(ev, "tool_use_id", None)
                tool_output = getattr(ev, "tool_output", None)
                is_error = getattr(ev, "is_error", False)
                error = getattr(ev, "error", None)

                if kind == "tool_use":
                    yield ToolCallEvent(
                        tool_name=tool_name,
                        params=tool_input or {},
                        tool_use_id=tool_use_id,
                    )
                elif kind == "tool_result":
                    yield ToolResultEvent(
                        tool_name=tool_name,
                        result={
                            "output": tool_output,
                            "is_error": False,
                        },
                    )
                elif kind == "tool_error":
                    yield ToolResultEvent(
                        tool_name=tool_name,
                        result={
                            "output": tool_output,
                            "error": error,
                            "is_error": True,
                        },
                    )
            except queue.Empty:
                if future.done():
                    break
                await asyncio.sleep(0.01)

        exit_code = await future
        result_text = stdout.getvalue()
        if result_text:
            yield TextDelta(content=result_text)

        reason = "success" if exit_code == 0 else f"exit_code={exit_code}"
        yield SessionComplete(reason=reason)

    async def run(self) -> dict[str, Any]:
        """Run to completion, return final result."""
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        async for event in self.stream():
            if isinstance(event, TextDelta):
                text_parts.append(event.content)
            elif isinstance(event, ToolCallEvent):
                tool_calls.append({"name": event.tool_name, "params": event.params})
            elif isinstance(event, SessionComplete):
                return {
                    "text": "".join(text_parts),
                    "reason": event.reason,
                    "tool_calls": tool_calls,
                }
        return {"text": "".join(text_parts), "reason": "unknown", "tool_calls": tool_calls}
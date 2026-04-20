"""Bridge between the synchronous agent loop and the Textual UI.

The agent loop (:func:`src.tool_system.agent_loop.run_agent_loop`) is
synchronous and performs blocking HTTP calls, so it runs on a worker
thread. This module owns that thread plus the translation layer that
marshals events back to the Textual screen:

* ``on_event(ToolEvent)``   → :class:`ToolEventMessage`.
* ``on_text_chunk(str)``    → :class:`AssistantChunk` (live streaming).
* permission request        → :class:`PermissionRequested` + blocking
  wait on a :class:`threading.Event`, letting the worker thread unblock
  only when the user has interacted with
  :class:`src.tui.screens.permission_modal.PermissionModal`.

Keeping this logic out of :class:`src.tui.app.ClawCodexTUI` lets unit
tests drive :class:`AgentBridge` with a fake agent loop (see
``tests/tui/test_agent_bridge.py``).
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from src.agent import Session
from src.tool_system.agent_loop import ToolEvent, run_agent_loop
from src.tool_system.context import ToolContext
from src.tool_system.registry import ToolRegistry
from src.utils.abort_controller import AbortController, AbortError

from .messages import (
    AgentRunFinished,
    AgentRunStarted,
    AssistantChunk,
    AssistantMessage,
    PermissionRequested,
    ToolEventMessage,
)
from .state import AppState


class AgentBridge:
    """Owns the agent-loop worker thread on behalf of the TUI."""

    def __init__(
        self,
        *,
        post_message: Callable[[Any], None],
        session: Session,
        provider: Any,
        tool_registry: ToolRegistry,
        tool_context: ToolContext,
        app_state: AppState,
        run_worker: Callable[..., Any],
        max_turns: int = 20,
        stream: bool = True,
    ) -> None:
        self._post = post_message
        self._session = session
        self._provider = provider
        self._tool_registry = tool_registry
        self._tool_context = tool_context
        self._state = app_state
        self._run_worker = run_worker
        self._max_turns = max_turns
        self._stream = stream
        self._busy_lock = threading.Lock()
        self._busy = False
        # Per-run abort controller. Created fresh in :meth:`submit` and
        # tripped by :meth:`cancel` (ESC from the prompt). The agent
        # loop checks the signal at safe boundaries; the streaming
        # callback also raises :class:`AbortError` on the worker thread
        # to tear down an in-flight HTTP stream cleanly.
        self._abort_controller: AbortController | None = None
        # Wire permission handler: the tool dispatcher calls this from
        # the worker thread, we post to the UI and block on an Event.
        tool_context.permission_handler = self._permission_handler

    # ---- public API ----
    @property
    def busy(self) -> bool:
        return self._busy

    def submit(self, prompt: str) -> bool:
        """Queue ``prompt`` for the agent. Returns False if busy."""

        with self._busy_lock:
            if self._busy:
                return False
            self._busy = True
            self._abort_controller = AbortController()

        self._session.conversation.add_user_message(prompt)
        self._post(AgentRunStarted(prompt=prompt))
        self._state.set_thinking(True, verb="Synthesizing")
        self._run_worker(
            self._run_agent_in_thread,
            thread=True,
            exclusive=True,
            name="agent-loop",
        )
        return True

    def cancel(self, reason: str = "user_interrupt") -> bool:
        """Trip the active run's abort signal. Returns True if a run was cancelled.

        Safe to call from any thread. The agent loop checks the signal
        at the next safe boundary (next turn, next tool call, next
        streaming chunk) and unwinds; ``_run_agent_in_thread`` then
        posts an ``AgentRunFinished`` and clears the busy flag.
        """

        with self._busy_lock:
            controller = self._abort_controller if self._busy else None
        if controller is None:
            return False
        controller.abort(reason)
        return True

    # ---- worker implementation ----
    def _run_agent_in_thread(self) -> None:
        controller = self._abort_controller

        def _on_event(event: ToolEvent) -> None:
            # Keep the app_state in sync so StatusLine / overlays can
            # observe in-progress tool ids.
            if event.kind == "tool_use" and event.tool_use_id:
                self._state.mark_tool_started(event.tool_use_id)
            elif event.kind in ("tool_result", "tool_error") and event.tool_use_id:
                self._state.mark_tool_finished(event.tool_use_id)
            self._post(
                ToolEventMessage(
                    kind=event.kind,
                    tool_name=event.tool_name,
                    tool_input=_safe_copy(event.tool_input),
                    tool_output=_safe_copy(event.tool_output),
                    tool_use_id=event.tool_use_id,
                    is_error=event.is_error,
                    error=event.error,
                )
            )

        def _on_text(chunk: str) -> None:
            # Bail out of the provider stream as soon as the user hits
            # ESC; raising from the callback breaks out of the
            # Anthropic SDK's ``with client.messages.stream(...)``
            # context manager and tears down the HTTP connection.
            if controller is not None and controller.signal.aborted:
                raise AbortError(controller.signal.reason or "user_interrupt")
            self._state.append_streaming_text(chunk)
            self._post(AssistantChunk(text=chunk))

        try:
            result = run_agent_loop(
                conversation=self._session.conversation,
                provider=self._provider,
                tool_registry=self._tool_registry,
                tool_context=self._tool_context,
                max_turns=self._max_turns,
                stream=self._stream,
                verbose=False,
                on_event=_on_event,
                on_text_chunk=_on_text if self._stream else None,
                cancel_signal=controller.signal if controller is not None else None,
            )
        except AbortError:
            self._post(
                AgentRunFinished(
                    response_text="",
                    num_turns=0,
                    usage=None,
                    error="Cancelled by user",
                )
            )
            self._finish()
            return
        except Exception as exc:  # pragma: no cover — surfaced to UI
            self._post(
                AgentRunFinished(
                    response_text="",
                    num_turns=0,
                    usage=None,
                    error=str(exc),
                )
            )
            self._finish()
            return

        self._post(AssistantMessage(text=result.response_text))
        if result.usage:
            try:
                self._state.usage.update(
                    {
                        "input_tokens": self._state.usage.get("input_tokens", 0)
                        + int(result.usage.get("input_tokens", 0) or 0),
                        "output_tokens": self._state.usage.get("output_tokens", 0)
                        + int(result.usage.get("output_tokens", 0) or 0),
                    }
                )
            except Exception:
                pass
        self._post(
            AgentRunFinished(
                response_text=result.response_text,
                num_turns=result.num_turns,
                usage=result.usage,
            )
        )
        self._finish()

    def _finish(self) -> None:
        self._state.set_thinking(False)
        self._state.clear_streaming_text()
        with self._busy_lock:
            self._busy = False

    # ---- permission bridge ----
    def _permission_handler(
        self,
        tool_name: str,
        message: str,
        suggestion: str | None,
    ) -> tuple[bool, bool]:
        """Called from the worker thread whenever the tool dispatcher
        wants user approval.

        Posts a :class:`PermissionRequested` to the UI, which pushes a
        modal; blocks the worker until the modal resolves via
        :class:`PermissionResolved`.
        """

        done = threading.Event()
        outcome: dict[str, bool] = {"allowed": False, "enable": False}

        def _decide(allowed: bool, enable: bool) -> None:
            outcome["allowed"] = allowed
            outcome["enable"] = enable
            done.set()

        pending = self._state.enqueue_permission(
            tool_name=tool_name,
            message=message,
            suggestion=suggestion,
            tool_input=None,
            decide=_decide,
        )
        self._post(
            PermissionRequested(
                request_id=pending.request_id,
                tool_name=tool_name,
                message=message,
                suggestion=suggestion,
                tool_input=None,
            )
        )
        # Wait for the UI to call ``_decide``. No timeout — the UI is
        # expected to always resolve the request (defaulting to deny on
        # Escape / Ctrl+C). A stuck permission will hold the worker
        # thread, which is the same failure mode as the legacy REPL's
        # ``input()`` call.
        done.wait()
        # Remove the entry from the state queue; the modal already
        # dismissed itself and emitted ``PermissionResolved``.
        self._state.resolve_permission(pending.request_id)
        return outcome["allowed"], outcome["enable"]


def _safe_copy(value: Any) -> Any:
    """Best-effort clone so the UI thread doesn't mutate tool-thread memory."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _safe_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_copy(v) for v in value]
    return value


__all__ = ["AgentBridge"]

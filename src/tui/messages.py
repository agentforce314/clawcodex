"""Textual ``Message`` subclasses used to push events from the agent-loop
worker thread (and other background tasks) into the UI.

All cross-thread communication between the worker that runs
``src.tool_system.agent_loop.run_agent_loop`` and the Textual widgets
goes through these messages via ``App.post_message``. Keeping the
payload primitive-only (``str``, ``dict``, ``bool``, ``set[str]``)
ensures Textual's message pump can marshal them safely across the
thread boundary.

Naming conventions mirror the React side:

* ``AgentRunStarted`` / ``AgentRunFinished`` — turn bracketing.
* ``AssistantChunk`` — a streamed assistant token batch
  (`handleMessageFromStream` counterpart).
* ``AssistantMessage`` — the fully-assembled assistant turn at end-of-turn.
* ``ToolEventMessage`` — proxies :class:`src.tool_system.agent_loop.ToolEvent`.
* ``PermissionRequested`` / ``PermissionResolved`` — gate-in / gate-out
  for the permission modal (Phase 1 of the ink :class:`PermissionRequest`
  overlay parity).
* ``StateChanged`` — a coarse "something in :class:`AppState` changed"
  signal used by status / transcript widgets that bind to many fields
  at once (we coalesce instead of emitting one message per field).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from textual.message import Message


@dataclass
class AgentRunStarted(Message):
    """Emitted right before the worker calls ``run_agent_loop``.

    Used by the status bar to flip into a 'thinking…' state.
    """

    prompt: str


@dataclass
class AssistantChunk(Message):
    """Streaming text chunk from the assistant.

    Unlike Phase 0 where chunks were buffered until end-of-turn, Phase 1
    widgets render chunks **live** into the active
    :class:`src.tui.widgets.messages.assistant_text.AssistantTextMessage`
    row via :meth:`AssistantTextMessage.append_chunk`.
    """

    text: str


@dataclass
class AssistantMessage(Message):
    """A complete assistant response at the end of a single agent turn.

    Also used to finalise the active streaming row (switching it from
    plain-text streaming mode to rendered Markdown) so we never show a
    half-parsed Markdown frame to the user.
    """

    text: str


@dataclass
class ToolEventMessage(Message):
    """A ``ToolEvent`` from the agent loop, flattened to dict for thread-safety.

    Fields mirror ``src.tool_system.agent_loop.ToolEvent``: ``kind`` is
    one of ``tool_use``, ``tool_result``, ``tool_error``.
    """

    kind: str
    tool_name: str
    tool_input: dict[str, Any] | None = None
    tool_output: Any | None = None
    tool_use_id: str | None = None
    is_error: bool = False
    error: str | None = None


@dataclass
class AgentRunFinished(Message):
    """Emitted when the agent loop returns (success or error)."""

    response_text: str
    num_turns: int
    usage: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class PermissionRequested(Message):
    """The tool dispatcher is asking the user to approve an action.

    The worker thread enqueues a :class:`src.tui.state.PendingPermission`
    on :class:`AppState` before posting this message; the screen reacts
    by pushing a :class:`~src.tui.screens.permission_modal.PermissionModal`.
    ``request_id`` correlates the modal's resolution back to the queued
    entry so multiple permission requests can be chained.
    """

    request_id: str
    tool_name: str
    message: str
    suggestion: str | None = None
    tool_input: dict[str, Any] | None = None


@dataclass
class PermissionResolved(Message):
    """Emitted by the permission modal once the user decides.

    Always paired with a call to :meth:`AppState.resolve_permission` so
    the worker thread is unblocked *before* this message is posted.
    """

    request_id: str
    allowed: bool
    enable_setting: bool = False


@dataclass
class StateChanged(Message):
    """Coalesced notification that :class:`AppState` was mutated.

    Widgets that want the full state re-read it from
    :class:`src.tui.app.ClawCodexTUI.app_state`. This keeps the message
    payload tiny; Textual's pump is happy to drop redundant ``StateChanged``
    messages if they arrive faster than the UI can process them.
    """

    hints: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class CancelRequested(Message):
    """User pressed ESC asking to cancel the in-flight agent run.

    Bubbles from :class:`src.tui.widgets.prompt_input.PromptInput` up to
    :class:`src.tui.app.ClawCodexTUI`, which decides whether to actually
    invoke ``AgentBridge.cancel()`` based on the current busy state.
    """

    pass

"""Tests for the TUI ``AskUserQuestion`` modal + bridge wiring.

The legacy TUI had a no-op ``ask_user`` lambda in
``src/tui/app.py:_build_default_tool_context`` that silently returned an
empty answer dict, so the ``AskUserQuestion`` tool could never collect
real answers in the Textual UI. This file covers the fix:

* :class:`src.tui.state.AppState` learned a ``pending_ask_users`` queue.
* :class:`src.tui.agent_bridge.AgentBridge` wires ``tool_context.ask_user``
  to a handler that posts a modal request and blocks the worker.
* :class:`src.tui.screens.ask_user_question.AskUserQuestionModal` collects
  answers and unblocks the worker.

The tests exercise the bridge end-to-end (worker thread → message → UI
side → decide → worker returns) plus the state queue and the modal's
answer-shaping logic.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import Mock

import pytest

from src.tui.agent_bridge import AgentBridge
from src.tui.messages import AskUserQuestionRequested
from src.tui.screens.ask_user_question import (
    AskUserQuestionModal,
    _QuestionPanel,
)
from src.tui.state import AppState, FocusedDialog, PendingAskUser


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _build_bridge() -> tuple[AgentBridge, list, AppState]:
    """Construct a minimal ``AgentBridge`` with a recording post_message.

    Returns ``(bridge, posted_messages, app_state)``. We don't spin up a
    real provider/session — the tests below only exercise
    ``_ask_user_handler``, which never touches them.
    """

    post_calls: list = []
    app_state = AppState(model="test-model", provider="test-provider")
    tool_context = __import__(
        "src.tool_system.context", fromlist=["ToolContext"]
    ).ToolContext(workspace_root="/tmp")
    bridge = AgentBridge(
        post_message=lambda msg: post_calls.append(msg),
        session=Mock(),
        provider=Mock(),
        tool_registry=Mock(),
        tool_context=tool_context,
        app_state=app_state,
        run_worker=lambda *_a, **_kw: None,
        max_turns=1,
        stream=False,
    )
    return bridge, post_calls, app_state


_QUESTIONS = [
    {
        "question": "每分钟发送的消息内容应该是什么？",
        "header": "msg",
        "multiSelect": False,
        "options": [
            {"label": "简单心跳提醒", "description": "每分钟发送一条简短的状态消息"},
            {"label": "健康检查报告", "description": "汇报工作树与运行中的任务"},
            {"label": "自定义内容", "description": "我会告诉你具体想发的内容"},
        ],
    },
]


# ----------------------------------------------------------------------
# State queue
# ----------------------------------------------------------------------


def test_state_enqueues_pending_ask_user_with_unique_id() -> None:
    state = AppState()
    decide = Mock()
    pending = state.enqueue_ask_user(_QUESTIONS, decide=decide)
    assert isinstance(pending, PendingAskUser)
    assert pending.questions == _QUESTIONS
    assert pending.decide is decide
    assert pending.request_id.startswith("ask-")
    assert state.pending_ask_users == [pending]


def test_state_resolve_ask_user_removes_entry() -> None:
    state = AppState()
    pending = state.enqueue_ask_user(_QUESTIONS, decide=Mock())
    state.resolve_ask_user(pending.request_id)
    assert state.pending_ask_users == []


def test_recompute_focus_promotes_elicitation_when_ask_user_pending() -> None:
    state = AppState()
    state.enqueue_ask_user(_QUESTIONS, decide=Mock())
    # ``ELICITATION`` is the existing TUI slot for ask-the-user style
    # dialogs (originally added for MCP elicitation). We reuse it for
    # ``AskUserQuestion`` so the focus router doesn't need a new entry.
    assert state.recompute_focus() == FocusedDialog.ELICITATION


def test_recompute_focus_falls_back_to_prompt_when_no_ask_user() -> None:
    state = AppState()
    assert state.recompute_focus() == FocusedDialog.PROMPT


# ----------------------------------------------------------------------
# Bridge wiring
# ----------------------------------------------------------------------


def test_bridge_wires_tool_context_ask_user() -> None:
    """Regression: the no-op lambda in ``_build_default_tool_context``
    was the only ask_user wiring — without the bridge taking over, the
    tool would receive ``{}`` and the agent loop would silently lose
    every clarifying question.
    """
    bridge, _, _ = _build_bridge()
    handler = bridge._tool_context.ask_user
    assert handler is not None
    # Bound methods are re-created on attribute access, so ``is`` is
    # unreliable — compare the underlying functions instead.
    assert handler.__func__ is bridge._ask_user_handler.__func__


def test_ask_user_handler_posts_request_and_blocks_until_decide() -> None:
    """Worker-thread → message → UI side → decide → worker returns."""
    bridge, posted, state = _build_bridge()

    result: dict = {}
    error: list[BaseException] = []

    def _worker() -> None:
        try:
            result["answers"] = bridge._ask_user_handler(list(_QUESTIONS))
        except BaseException as exc:  # pragma: no cover - surfaced via assertion
            error.append(exc)

    t = threading.Thread(target=_worker, name="ask-user-worker")
    t.start()

    # The handler must post the message and enqueue before blocking.
    deadline = time.monotonic() + 1.0
    while not posted and time.monotonic() < deadline:
        time.sleep(0.01)
    assert posted, "AskUserQuestionRequested was never posted"
    assert state.pending_ask_users, "PendingAskUser was never enqueued"

    posted_msg = posted[0]
    assert isinstance(posted_msg, AskUserQuestionRequested)
    assert posted_msg.questions == _QUESTIONS
    assert state.pending_ask_users[0].request_id == posted_msg.request_id

    # Simulate the UI side: pop the pending, call decide with an
    # answer, then drain the state queue the way the real modal does.
    pending = state.pending_ask_users[0]
    pending.decide({_QUESTIONS[0]["question"]: "心跳一下 ⏰"})
    t.join(timeout=2.0)
    assert not t.is_alive(), "worker thread is still blocked after decide()"
    assert not error
    assert result["answers"] == {_QUESTIONS[0]["question"]: "心跳一下 ⏰"}
    assert state.pending_ask_users == [], "state queue not drained after decide"


def test_ask_user_handler_returns_empty_dict_on_cancellation() -> None:
    """User pressed Esc → modal dismisses with ``None`` → handler
    returns ``{}`` so the agent loop can recover."""
    bridge, posted, state = _build_bridge()

    result: dict = {}

    def _worker() -> None:
        result["answers"] = bridge._ask_user_handler(list(_QUESTIONS))

    t = threading.Thread(target=_worker, name="ask-user-worker")
    t.start()
    deadline = time.monotonic() + 1.0
    while not posted and time.monotonic() < deadline:
        time.sleep(0.01)
    pending = state.pending_ask_users[0]
    pending.decide(None)  # user cancelled
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert result["answers"] == {}


def test_ask_user_handler_resolves_state_after_completion() -> None:
    """After the worker returns, the pending entry must be gone so the
    next ask_user call gets a fresh ``ask-N`` id (and the focus router
    drops back to PROMPT)."""
    bridge, posted, state = _build_bridge()
    pending_id = None

    def _worker() -> None:
        bridge._ask_user_handler(list(_QUESTIONS))

    t = threading.Thread(target=_worker, name="ask-user-worker")
    t.start()
    deadline = time.monotonic() + 1.0
    while not state.pending_ask_users and time.monotonic() < deadline:
        time.sleep(0.01)
    pending_id = state.pending_ask_users[0].request_id
    state.pending_ask_users[0].decide({_QUESTIONS[0]["question"]: "x"})
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert all(p.request_id != pending_id for p in state.pending_ask_users)
    assert state.recompute_focus() == FocusedDialog.PROMPT


# ----------------------------------------------------------------------
# Modal answer shaping
# ----------------------------------------------------------------------


def test_question_panel_defaults_to_first_option_when_unselected() -> None:
    panel = _QuestionPanel(_QUESTIONS[0], 0)
    # No selection → falls back to the first option's label.
    assert panel.build_answer() == "简单心跳提醒"


def test_question_panel_returns_chosen_label() -> None:
    panel = _QuestionPanel(_QUESTIONS[0], 0)
    panel.select_option(1)  # second option: 健康检查报告
    assert panel.build_answer() == "健康检查报告"


def test_question_panel_other_returns_free_text() -> None:
    panel = _QuestionPanel(_QUESTIONS[0], 0)
    panel.select_option(-1)  # "Other"
    panel.update_other_text("  每分钟发个笑话  ")
    assert panel.build_answer() == "每分钟发个笑话"


def test_question_panel_other_ignores_blank_free_text_and_falls_back() -> None:
    panel = _QuestionPanel(_QUESTIONS[0], 0)
    panel.select_option(-1)
    panel.update_other_text("   ")
    # No real free text → fall back to the first option rather than
    # returning empty string (legacy REPL parity).
    assert panel.build_answer() == "简单心跳提醒"


def test_question_panel_single_select_clears_previous() -> None:
    panel = _QuestionPanel(_QUESTIONS[0], 0)
    panel.select_option(0)
    panel.select_option(1)
    assert panel.build_answer() == "健康检查报告"


def test_question_panel_multiselect_joins_in_pick_order() -> None:
    multi = {
        "question": "Pick fruits",
        "header": "f",
        "multiSelect": True,
        "options": [
            {"label": "Apple", "description": ""},
            {"label": "Banana", "description": ""},
            {"label": "Cherry", "description": ""},
        ],
    }
    panel = _QuestionPanel(multi, 0)
    panel.select_option(2)  # Cherry
    panel.select_option(0)  # Apple
    assert panel.build_answer() == "Cherry, Apple"


def test_question_panel_multiselect_toggle_off() -> None:
    multi = {
        "question": "Pick fruits",
        "multiSelect": True,
        "options": [
            {"label": "Apple", "description": ""},
            {"label": "Banana", "description": ""},
        ],
    }
    panel = _QuestionPanel(multi, 0)
    panel.select_option(0)
    panel.select_option(0)  # toggle off
    assert panel.build_answer() == ""


def test_modal_imports_and_screen_subclass() -> None:
    """Sanity: the modal can be imported and is a Textual ``ModalScreen``."""
    from textual.screen import ModalScreen

    assert issubclass(AskUserQuestionModal, ModalScreen)

"""Task-tool reminder — port of ``getTaskReminderAttachments``.

Reference: typescript/src/utils/attachments.ts:3658-3771 (turn counting +
gates) and typescript/src/utils/messages.ts:2606-2628 (reminder text).
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

from src.bootstrap.state import set_is_interactive
from src.context_system.plan_mode import wrap_in_system_reminder
from src.context_system.task_reminder import (
    TURNS_BETWEEN_REMINDERS,
    TURNS_SINCE_WRITE,
    build_task_reminder_attachment,
    get_task_reminder_turn_counts,
)


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


TASK_TOOLS = [_Tool("TaskCreate"), _Tool("TaskUpdate"), _Tool("Bash")]


def assistant(*blocks: dict) -> dict:
    return {"role": "assistant", "content": list(blocks) or [{"type": "text", "text": "ok"}]}


def user(text: str = "hi") -> dict:
    return {"role": "user", "content": text}


def tool_use(name: str) -> dict:
    return {"type": "tool_use", "id": "t1", "name": name, "input": {}}


def reminder_msg() -> dict:
    """A previously-persisted reminder, exactly as the wiring stores it."""
    return {
        "role": "user",
        "content": wrap_in_system_reminder(
            "The task tools haven't been used recently. …"
        ),
    }


def turns(n: int) -> list[dict]:
    out: list[dict] = []
    for _ in range(n):
        out.append(user())
        out.append(assistant())
    return out


@pytest.fixture()
def interactive():
    set_is_interactive(True)
    yield
    set_is_interactive(False)


# ---------------------------------------------------------------------------
# Turn counting (getTaskReminderTurnCounts)
# ---------------------------------------------------------------------------


def test_counts_assistant_turns() -> None:
    assert get_task_reminder_turn_counts(turns(4)) == (4, 4)


def test_task_tool_turn_resets_and_does_not_count_itself() -> None:
    # The reference checks the tool_use BEFORE incrementing, so the turn that
    # itself manages tasks contributes zero to the management counter.
    messages = [*turns(5), assistant(tool_use("TaskCreate")), *turns(3)]
    assert get_task_reminder_turn_counts(messages) == (3, 9)


def test_task_update_counts_as_management_too() -> None:
    messages = [*turns(2), assistant(tool_use("TaskUpdate")), *turns(1)]
    assert get_task_reminder_turn_counts(messages)[0] == 1


def test_thinking_only_messages_are_not_turns() -> None:
    thinking = assistant({"type": "thinking", "thinking": "hmm", "signature": ""})
    messages = [*turns(2), thinking, thinking]
    assert get_task_reminder_turn_counts(messages) == (2, 2)


def test_prior_reminder_freezes_the_reminder_counter() -> None:
    messages = [*turns(20), reminder_msg(), *turns(3)]
    since_mgmt, since_reminder = get_task_reminder_turn_counts(messages)
    assert since_reminder == 3
    assert since_mgmt == 23


# ---------------------------------------------------------------------------
# Builder gates (getTaskReminderAttachments)
# ---------------------------------------------------------------------------


def _due() -> list[dict]:
    return turns(TURNS_SINCE_WRITE)


def test_fires_with_the_reference_text(interactive) -> None:
    texts = build_task_reminder_attachment(_due(), tools=TASK_TOOLS)

    assert len(texts) == 1
    assert texts[0].startswith("The task tools haven't been used recently")
    assert "consider using TaskCreate" in texts[0]
    assert "TaskUpdate to update task status" in texts[0]
    assert "NEVER mention this reminder to the user" in texts[0]
    assert "Here are the existing tasks" not in texts[0]


def test_lists_existing_tasks(interactive) -> None:
    tasks = {"a1": {"id": "a1", "subject": "Ship it", "status": "pending"}}
    texts = build_task_reminder_attachment(_due(), tools=TASK_TOOLS, tasks=tasks)

    assert "Here are the existing tasks:" in texts[0]
    assert "#a1. [pending] Ship it" in texts[0]


def test_headless_gets_no_reminder() -> None:
    # The deliberate delta from the reference: the TB2.1 measurements that
    # damped the prompt bullet apply here with the same force.
    assert build_task_reminder_attachment(_due(), tools=TASK_TOOLS) == []


def test_throttled_until_enough_turns_since_write(interactive) -> None:
    assert build_task_reminder_attachment(turns(TURNS_SINCE_WRITE - 1), tools=TASK_TOOLS) == []


def test_throttled_after_a_recent_reminder(interactive) -> None:
    messages = [*turns(20), reminder_msg(), *turns(TURNS_BETWEEN_REMINDERS - 1)]
    assert build_task_reminder_attachment(messages, tools=TASK_TOOLS) == []

    messages = [*turns(20), reminder_msg(), *turns(TURNS_BETWEEN_REMINDERS)]
    assert build_task_reminder_attachment(messages, tools=TASK_TOOLS) != []


def test_recent_task_management_resets_the_clock(interactive) -> None:
    messages = [*turns(20), assistant(tool_use("TaskUpdate")), *turns(2)]
    assert build_task_reminder_attachment(messages, tools=TASK_TOOLS) == []


def test_requires_task_update_in_the_toolkit(interactive) -> None:
    assert build_task_reminder_attachment(_due(), tools=[_Tool("Bash")]) == []


def test_brief_presence_does_not_disable_the_reminder(interactive) -> None:
    # The reference's Brief gate (attachments.ts:3731) is deliberately not
    # ported: clawcodex registers Brief unconditionally, so the transplanted
    # gate disabled the reminder on every surface (caught by the wiring test).
    assert build_task_reminder_attachment(_due(), tools=[*TASK_TOOLS, _Tool("Brief")]) != []


def test_empty_conversation_gets_no_reminder(interactive) -> None:
    assert build_task_reminder_attachment([], tools=TASK_TOOLS) == []


def test_kill_switch_env(interactive) -> None:
    with mock.patch.dict(os.environ, {"OPENCLAUDE_DISABLE_TOOL_REMINDERS": "1"}):
        assert build_task_reminder_attachment(_due(), tools=TASK_TOOLS) == []


def test_ant_users_are_skipped(interactive) -> None:
    with mock.patch.dict(os.environ, {"USER_TYPE": "ant"}):
        assert build_task_reminder_attachment(_due(), tools=TASK_TOOLS) == []

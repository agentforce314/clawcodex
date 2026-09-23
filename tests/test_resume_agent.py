"""Resume validation and transcript decoding; live runs are covered end-to-end."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.agent.resume_agent import (
    AgentContinuation,
    _reconstruct_messages_from_transcript,
    resume_agent_background,
)
from src.agent.transcript import TranscriptWriter
from src.tasks.local_agent import complete_agent_task, register_async_agent
from src.tool_system.context import ToolContext
from src.types.content_blocks import ToolUseBlock
from src.types.messages import AssistantMessage, UserMessage


@pytest.mark.parametrize("status", ["completed", "failed", "killed"])
def test_missing_launcher_never_creates_a_ghost_worker(tmp_path: Path, status):
    from dataclasses import replace

    context = ToolContext(workspace_root=tmp_path)
    state = register_async_agent(
        agent_id="worker",
        description="Research",
        prompt="original",
        agent_type="general-purpose",
        registry=context.runtime_tasks,
    )
    state = replace(state, status=status)
    context.runtime_tasks.upsert(state)
    for _ in range(2):
        result = asyncio.run(
            resume_agent_background(
                agent_id="worker",
                prompt="follow up",
                context=context,
            )
        )
        assert not result.resumed
        assert "no executable continuation" in result.reason
        assert context.runtime_tasks.get("worker") is state
    assert not context.task_manager.list()


def test_resume_returns_noop_for_missing_task(tmp_path: Path):
    result = asyncio.run(
        resume_agent_background(
            agent_id="missing",
            prompt="follow up",
            context=ToolContext(workspace_root=tmp_path),
        )
    )
    assert not result.resumed
    assert "not found" in result.reason


def test_resume_returns_noop_for_running_task(tmp_path: Path):
    context = ToolContext(workspace_root=tmp_path)
    original = register_async_agent(
        agent_id="worker",
        description="Research",
        prompt="original",
        agent_type="general-purpose",
        registry=context.runtime_tasks,
    )
    result = asyncio.run(
        resume_agent_background(
            agent_id="worker",
            prompt="follow up",
            context=context,
        )
    )
    assert not result.resumed
    assert "not terminal" in result.reason
    assert context.runtime_tasks.get("worker") is original


def test_launcher_failure_leaves_terminal_state_intact(tmp_path: Path):
    context = ToolContext(workspace_root=tmp_path)
    register_async_agent(
        agent_id="worker",
        description="Research",
        prompt="original",
        agent_type="general-purpose",
        registry=context.runtime_tasks,
    )
    complete_agent_task("worker", result_text="done", registry=context.runtime_tasks)
    original = context.runtime_tasks.get("worker")

    def reject(prompt, history):
        raise RuntimeError("admission refused")

    continuation = AgentContinuation(reject, str(tmp_path / "missing.jsonl"))
    continuation.finished.set()
    context.agent_continuations["worker"] = continuation
    result = asyncio.run(
        resume_agent_background(
            agent_id="worker",
            prompt="follow up",
            context=context,
        )
    )
    assert not result.resumed
    assert result.reason == "admission refused"
    assert context.runtime_tasks.get("worker") is original


def test_transcript_replay_restores_typed_messages_and_tolerates_partial_tail(
    tmp_path: Path,
):
    path = tmp_path / "history.jsonl"
    with TranscriptWriter(str(path)) as writer:
        writer.append(UserMessage(content="read source"))
        writer.append(
            AssistantMessage(
                content=[
                    ToolUseBlock(
                        id="read1", name="Read", input={"file_path": "source.txt"}
                    )
                ]
            )
        )
    with path.open("a") as stream:
        stream.write('{"role": "user",')
    messages = _reconstruct_messages_from_transcript(str(path))
    assert len(messages) == 2
    assert isinstance(messages[0], UserMessage)
    assert isinstance(messages[1], AssistantMessage)
    assert isinstance(messages[1].content[0], ToolUseBlock)
    assert messages[1].content[0].id == "read1"

"""Real Agent/Workflow queries must write only in their requested checkout."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from tests.test_multi_agent_runtime_e2e import wait_finished


class WriteProvider:
    model = "worktree-test"

    def __init__(self):
        self.requests = []

    def chat_stream_response(self, *args, **kwargs):
        raise NotImplementedError

    def chat(self, messages, **kwargs):
        self.requests.append(messages)
        user = next(m for m in reversed(messages) if m.get("role") == "user")
        content = user.get("content")
        finished = isinstance(content, list) and any(
            block.get("type") == "tool_result" for block in content
        )
        return ChatResponse(
            content="Artifact written" if finished else "Writing the artifact",
            model=self.model,
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop" if finished else "tool_use",
            tool_uses=(
                None
                if finished
                else [
                    {
                        "id": f"write-{len(self.requests)}",
                        "name": "Write",
                        "input": {
                            "file_path": "result.txt",
                            "content": "isolated artifact",
                        },
                    }
                ]
            ),
        )


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path / "config"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
        ["init"],
        ["config", "user.name", "Runtime test"],
        ["config", "user.email", "test@example.com"],
    ):
        subprocess.run(["git", *command], cwd=repo, check=True, capture_output=True)
    (repo / "source.txt").write_text("original")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


@pytest.mark.parametrize("background", [False, True])
def test_agent_isolation_preserves_real_write(isolated_repo, background):
    provider = WriteProvider()
    context = ToolContext(workspace_root=isolated_repo)
    registry = build_default_registry(provider=provider)
    result = registry.dispatch(
        ToolCall(
            name="Agent",
            input={
                "prompt": "Write the result file",
                "description": "Isolated write",
                "isolation": "worktree",
                "run_in_background": background,
            },
        ),
        context,
    )
    assert not result.is_error, result.output
    output = result.output
    if background:
        state = wait_finished(context, output["agent_id"])
        assert state.status == "completed", state.error
        assert "Worktree changes preserved" in state.result_text
    worktree = Path(output["worktree_path"])
    assert (worktree / "result.txt").read_text() == "isolated artifact"
    assert not (isolated_repo / "result.txt").exists()
    assert len(provider.requests) == 2


def test_agent_isolation_failure_never_starts_model(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path / "config"))
    provider = WriteProvider()
    context = ToolContext(workspace_root=tmp_path)
    registry = build_default_registry(provider=provider)
    with pytest.raises(RuntimeError, match="Git repository"):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "prompt": "Write",
                    "description": "Must isolate",
                    "isolation": "worktree",
                },
            ),
            context,
        )
    assert not provider.requests
    assert context.agent_supervisor.live_count() == 0
    assert not (tmp_path / "result.txt").exists()


async def test_workflow_isolation_preserves_real_write(isolated_repo):
    from src.agent.agent_definitions import get_built_in_agents
    from src.utils.abort_controller import AbortController
    from src.workflow.runner import LiveAgentRunner
    from src.workflow.types import AgentSpec

    provider = WriteProvider()
    context = ToolContext(workspace_root=isolated_repo)
    registry = build_default_registry(provider=provider)
    agent = next(a for a in get_built_in_agents() if a.agent_type == "general-purpose")
    runner = LiveAgentRunner(
        provider=provider,
        parent_context=context,
        tool_registry=registry,
        base_tools=registry.list_tools(),
        resolve_agent=lambda _: agent,
        run_id="wf_isolation",
    )
    outcome = await runner.run(
        AgentSpec(prompt="Write the result file", isolation="worktree"),
        abort=AbortController(),
        index="0",
    )
    assert not outcome.error
    assert outcome.worktree_path and outcome.worktree_path in outcome.text
    assert (
        Path(outcome.worktree_path) / "result.txt"
    ).read_text() == "isolated artifact"
    assert not (isolated_repo / "result.txt").exists()
    assert context.agent_supervisor.live_count() == 0


def test_fork_worktree_survives_until_background_descendant_writes(
    isolated_repo, monkeypatch
):
    """A clean fork may finish before a background child makes its first edit."""
    monkeypatch.setenv("CLAUDE_FORK_SUBAGENT", "1")
    gate = threading.Event()

    class DelegateProvider(WriteProvider):
        def chat(self, messages, **kwargs):
            if "DELEGATE_CHILD_NOW" in json.dumps(messages):
                last = next(m for m in reversed(messages) if m.get("role") == "user")
                content = last.get("content")
                done = isinstance(content, list) and any(
                    b.get("type") == "tool_result" for b in content
                )
                return ChatResponse(
                    content="Child launched" if done else "Delegate",
                    model=self.model,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    finish_reason="stop" if done else "tool_use",
                    tool_uses=(
                        None
                        if done
                        else [
                            {
                                "id": "delegate-child",
                                "name": "Agent",
                                "input": {
                                    "name": "delayed-writer",
                                    "subagent_type": "general-purpose",
                                    "prompt": "Write a result file",
                                    "description": "Deferred edit",
                                    "run_in_background": True,
                                },
                            }
                        ]
                    ),
                )
            assert gate.wait(8), "parent did not release descendant"
            return super().chat(messages, **kwargs)

    provider = DelegateProvider()
    context = ToolContext(workspace_root=isolated_repo)
    registry = build_default_registry(provider=provider)
    try:
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "prompt": "DELEGATE_CHILD_NOW",
                    "description": "Fork with a child",
                    "isolation": "worktree",
                },
            ),
            context,
        )
        assert not result.is_error, result.output
        path = Path(result.output["worktree_path"])
        assert path.is_dir()
        child_id = context.agent_name_registry.get("delayed-writer")
        assert child_id
    finally:
        gate.set()
        for task in context.task_manager.list():
            task.thread.join(timeout=8)
            assert not task.thread.is_alive()
    assert wait_finished(context, child_id).status == "completed"
    assert (path / "result.txt").read_text() == "isolated artifact"
    assert not (isolated_repo / "result.txt").exists()

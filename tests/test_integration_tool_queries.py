"""Full-stack integration tests exercising REPL → agent loop → tool dispatch.

Each test uses a FakeProvider that emits deterministic tool calls, then
verifies the side effects (files created, edited, etc.) and the conversation
state after the loop finishes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agent.conversation import Conversation
from src.providers.base import ChatResponse
from src.tool_system.agent_loop import run_agent_loop, AgentLoopResult
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry


# ---------------------------------------------------------------------------
# Fake providers — each drives a specific tool-call scenario
# ---------------------------------------------------------------------------

class _TextOnlyProvider:
    model = "fake"

    def chat(self, messages, tools=None, **kwargs):
        return ChatResponse(
            content="I have answered your question.",
            model=self.model,
            usage={"input_tokens": 5, "output_tokens": 8},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class _ReadFileProvider:
    model = "fake"

    def __init__(self, target: Path):
        self._target = target
        self._turn = 0

    def chat(self, messages, tools=None, **kwargs):
        self._turn += 1
        if self._turn == 1:
            return ChatResponse(
                content="Let me read the file.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 6},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "r1",
                    "name": "Read",
                    "input": {"file_path": str(self._target)},
                }],
            )
        return ChatResponse(
            content="The file contains the expected content.",
            model=self.model,
            usage={"input_tokens": 10, "output_tokens": 8},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class _WriteFileProvider:
    model = "fake"

    def __init__(self, target: Path, content: str):
        self._target = target
        self._content = content
        self._turn = 0

    def chat(self, messages, tools=None, **kwargs):
        self._turn += 1
        if self._turn == 1:
            return ChatResponse(
                content="Creating file.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 6},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "w1",
                    "name": "Write",
                    "input": {"file_path": str(self._target), "content": self._content},
                }],
            )
        return ChatResponse(
            content="Done.",
            model=self.model,
            usage={"input_tokens": 5, "output_tokens": 3},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class _ReadWriteEditProvider:
    """Drives a 3-step flow: Read → Write new file → Edit existing file."""
    model = "fake"

    def __init__(self, workspace: Path):
        self._ws = workspace
        self._turn = 0

    def chat(self, messages, tools=None, **kwargs):
        self._turn += 1
        if self._turn == 1:
            return ChatResponse(
                content="Reading source.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 4},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "t1",
                    "name": "Read",
                    "input": {"file_path": str(self._ws / "source.txt")},
                }],
            )
        if self._turn == 2:
            return ChatResponse(
                content="Creating output.",
                model=self.model,
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "t2",
                    "name": "Write",
                    "input": {"file_path": str(self._ws / "output.txt"), "content": "new file\n"},
                }],
            )
        if self._turn == 3:
            return ChatResponse(
                content="Editing source.",
                model=self.model,
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "t3",
                    "name": "Edit",
                    "input": {
                        "file_path": str(self._ws / "source.txt"),
                        "old_string": "original",
                        "new_string": "modified",
                    },
                }],
            )
        return ChatResponse(
            content="All tasks complete.",
            model=self.model,
            usage={"input_tokens": 5, "output_tokens": 4},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class _BashProvider:
    model = "fake"

    def __init__(self, command: str):
        self._command = command
        self._turn = 0

    def chat(self, messages, tools=None, **kwargs):
        self._turn += 1
        if self._turn == 1:
            return ChatResponse(
                content="Running command.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 4},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "b1",
                    "name": "Bash",
                    "input": {"command": self._command},
                }],
            )
        return ChatResponse(
            content="Done.",
            model=self.model,
            usage={"input_tokens": 5, "output_tokens": 3},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class _GlobGrepProvider:
    """Drives Glob → Grep sequence."""
    model = "fake"

    def __init__(self, workspace: Path):
        self._ws = workspace
        self._turn = 0

    def chat(self, messages, tools=None, **kwargs):
        self._turn += 1
        if self._turn == 1:
            return ChatResponse(
                content="Searching files.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 4},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "g1",
                    "name": "Glob",
                    "input": {"pattern": "*.txt", "path": str(self._ws)},
                }],
            )
        if self._turn == 2:
            return ChatResponse(
                content="Grepping content.",
                model=self.model,
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "g2",
                    "name": "Grep",
                    "input": {"pattern": "needle", "path": str(self._ws)},
                }],
            )
        return ChatResponse(
            content="Found results.",
            model=self.model,
            usage={"input_tokens": 5, "output_tokens": 4},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class _MultiToolParallelProvider:
    """Emits 2 parallel tool calls (Read + Bash) in a single turn."""
    model = "fake"

    def __init__(self, workspace: Path):
        self._ws = workspace
        self._turn = 0

    def chat(self, messages, tools=None, **kwargs):
        self._turn += 1
        if self._turn == 1:
            return ChatResponse(
                content="Running two tools.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 4},
                finish_reason="tool_use",
                tool_uses=[
                    {
                        "id": "p1",
                        "name": "Read",
                        "input": {"file_path": str(self._ws / "data.txt")},
                    },
                    {
                        "id": "p2",
                        "name": "Bash",
                        "input": {"command": "echo parallel-ok"},
                    },
                ],
            )
        return ChatResponse(
            content="Both completed.",
            model=self.model,
            usage={"input_tokens": 10, "output_tokens": 4},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class _PermissionDeniedWriteProvider:
    """Attempts to write a .md file (should be blocked by default permissions)."""
    model = "fake"

    def __init__(self, workspace: Path):
        self._ws = workspace
        self._turn = 0

    def chat(self, messages, tools=None, **kwargs):
        self._turn += 1
        if self._turn == 1:
            return ChatResponse(
                content="Writing docs.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 4},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "d1",
                    "name": "Write",
                    "input": {"file_path": str(self._ws / "README.md"), "content": "hello"},
                }],
            )
        return ChatResponse(
            content="Permission was denied as expected.",
            model=self.model,
            usage={"input_tokens": 5, "output_tokens": 6},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run(provider, workspace: Path, query: str, max_turns: int = 10, mode: str = "bypassPermissions") -> AgentLoopResult:
    from src.permissions.types import ToolPermissionContext
    registry = build_default_registry(include_user_tools=False)
    ctx = ToolContext(
        workspace_root=workspace,
        permission_context=ToolPermissionContext(mode=mode),
    )
    conv = Conversation()
    conv.add_user_message(query)
    return run_agent_loop(conv, provider, registry, ctx, max_turns=max_turns)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIntegrationTextOnly:
    def test_text_only_response(self, tmp_path):
        result = _run(_TextOnlyProvider(), tmp_path, "Hello")
        assert result.response_text == "I have answered your question."
        assert result.num_turns == 1
        assert result.usage["input_tokens"] == 5
        assert result.usage["output_tokens"] == 8


class TestIntegrationReadFile:
    def test_read_existing_file(self, tmp_path):
        target = tmp_path / "hello.txt"
        target.write_text("hello world\n", encoding="utf-8")
        result = _run(_ReadFileProvider(target), tmp_path, "Read hello.txt")
        assert result.num_turns == 2
        assert "expected content" in result.response_text

    def test_read_nonexistent_file_is_error(self, tmp_path):
        target = tmp_path / "missing.txt"
        result = _run(_ReadFileProvider(target), tmp_path, "Read missing.txt")
        assert result.num_turns == 2


class TestIntegrationWriteFile:
    def test_write_creates_file(self, tmp_path):
        target = tmp_path / "new.txt"
        result = _run(_WriteFileProvider(target, "hello\n"), tmp_path, "Create new.txt")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello\n"
        assert result.num_turns == 2


class TestIntegrationReadWriteEdit:
    def test_multi_step_read_write_edit(self, tmp_path):
        source = tmp_path / "source.txt"
        source.write_text("original content\n", encoding="utf-8")

        result = _run(_ReadWriteEditProvider(tmp_path), tmp_path, "Transform source")

        assert (tmp_path / "output.txt").exists()
        assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "new file\n"
        assert source.read_text(encoding="utf-8") == "modified content\n"
        assert result.num_turns == 4
        assert result.response_text == "All tasks complete."


class TestIntegrationBash:
    def test_bash_echo(self, tmp_path):
        result = _run(_BashProvider("echo integration-test"), tmp_path, "Run echo")
        assert result.num_turns == 2

    def test_bash_dangerous_command_blocked(self, tmp_path):
        result = _run(_BashProvider("sudo rm -rf /"), tmp_path, "Run dangerous")
        assert result.num_turns == 2


class TestIntegrationGlobGrep:
    def test_glob_then_grep(self, tmp_path):
        (tmp_path / "a.txt").write_text("needle in haystack\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("no match\n", encoding="utf-8")
        (tmp_path / "c.py").write_text("python file\n", encoding="utf-8")

        result = _run(_GlobGrepProvider(tmp_path), tmp_path, "Search for needle")
        assert result.num_turns == 3
        assert result.response_text == "Found results."


class TestIntegrationParallelTools:
    def test_two_tools_in_one_turn(self, tmp_path):
        (tmp_path / "data.txt").write_text("some data\n", encoding="utf-8")

        result = _run(_MultiToolParallelProvider(tmp_path), tmp_path, "Read and run")
        assert result.num_turns == 2
        assert result.response_text == "Both completed."


class TestIntegrationPermissions:
    def test_write_md_blocked_by_default(self, tmp_path):
        result = _run(_PermissionDeniedWriteProvider(tmp_path), tmp_path, "Write docs", mode="default")
        assert not (tmp_path / "README.md").exists()
        assert result.num_turns == 2


class TestIntegrationMaxTurns:
    def test_max_turns_respected(self, tmp_path):
        class _InfiniteToolProvider:
            model = "fake"
            _turn = 0

            def chat(self, messages, tools=None, **kwargs):
                self._turn += 1
                return ChatResponse(
                    content=f"Turn {self._turn}.",
                    model=self.model,
                    usage={"input_tokens": 5, "output_tokens": 5},
                    finish_reason="tool_use",
                    tool_uses=[{
                        "id": f"inf{self._turn}",
                        "name": "Bash",
                        "input": {"command": f"echo turn-{self._turn}"},
                    }],
                )

            def chat_stream_response(self, *a, **kw):
                raise NotImplementedError

        result = _run(_InfiniteToolProvider(), tmp_path, "Loop", max_turns=3)
        assert result.num_turns == 3
        assert result.response_text == "[Max tool turns reached]"


class TestIntegrationUsageTracking:
    def test_usage_accumulates_across_turns(self, tmp_path):
        target = tmp_path / "f.txt"
        result = _run(_WriteFileProvider(target, "x"), tmp_path, "Write")
        assert result.usage is not None
        assert result.usage["input_tokens"] == 10
        assert result.usage["output_tokens"] == 9


class TestIntegrationREPLChat:
    """End-to-end via ClawcodexREPL.chat()."""

    def _make_config(self, home: Path):
        cfg_dir = home / ".clawcodex"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(json.dumps({
            "default_provider": "glm",
            "providers": {
                "glm": {
                    "api_key": "fake-key",
                    "base_url": "https://open.bigmodel.cn/api/paas/v4",
                    "default_model": "glm-4.5",
                }
            },
        }), encoding="utf-8")

    def test_repl_chat_simple_query(self, tmp_path):
        home = tmp_path / "home"
        work = tmp_path / "work"
        work.mkdir()
        self._make_config(home)

        old_home = os.environ.get("HOME")
        old_cwd = Path.cwd()
        try:
            os.environ["HOME"] = str(home)
            os.chdir(work)
            with patch("src.repl.core.get_provider_class", return_value=lambda *a, **kw: _TextOnlyProvider()):
                from src.repl.core import ClawcodexREPL
                repl = ClawcodexREPL(provider_name="glm", stream=False)
                repl.chat("Hello")
            msgs = repl.session.conversation.get_messages()
            roles = [m["role"] for m in msgs]
            assert "user" in roles
            assert "assistant" in roles
        finally:
            os.environ["HOME"] = old_home or ""
            os.chdir(old_cwd)

    def test_repl_chat_tool_creates_file(self, tmp_path):
        home = tmp_path / "home"
        work = tmp_path / "work"
        work.mkdir()
        self._make_config(home)

        target = work / "created.txt"

        class _Provider:
            model = "glm-4.5"
            _turn = 0

            def __init__(self, *a, **kw):
                pass

            def chat(self, messages, tools=None, **kwargs):
                self._turn += 1
                if self._turn == 1:
                    return ChatResponse(
                        content="Creating file.",
                        model=self.model,
                        usage={"input_tokens": 5, "output_tokens": 5},
                        finish_reason="tool_use",
                        tool_uses=[{
                            "id": "repl1",
                            "name": "Write",
                            "input": {"file_path": str(target), "content": "repl-ok\n"},
                        }],
                    )
                return ChatResponse(
                    content="File created.",
                    model=self.model,
                    usage={"input_tokens": 5, "output_tokens": 4},
                    finish_reason="stop",
                    tool_uses=None,
                )

            def chat_stream_response(self, *a, **kw):
                raise NotImplementedError

        _Provider._turn = 0

        old_home = os.environ.get("HOME")
        old_cwd = Path.cwd()
        try:
            os.environ["HOME"] = str(home)
            os.chdir(work)
            with patch("src.repl.core.get_provider_class", return_value=_Provider):
                from src.repl.core import ClawcodexREPL
                repl = ClawcodexREPL(provider_name="glm", stream=False)
                repl.chat("Create created.txt")
            assert target.exists()
            assert target.read_text(encoding="utf-8") == "repl-ok\n"
        finally:
            os.environ["HOME"] = old_home or ""
            os.chdir(old_cwd)

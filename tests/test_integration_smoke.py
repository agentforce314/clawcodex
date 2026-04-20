"""Integration smoke tests for the claw-codex app.

Exercises the full stack (REPL → conversation → tool loop → provider)
using a deterministic FakeProvider so no real API calls are made.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.providers.base import ChatResponse


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------

class _FakeProvider:
    """Deterministic provider stub for integration testing."""

    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or "glm-4.5"
        self._calls: list[list] = []

    def chat(self, messages, tools=None, **kwargs):
        self._calls.append(messages)
        call_n = len(self._calls)

        if call_n == 1:
            return ChatResponse(
                content="Hello from integration smoke test.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 10},
                finish_reason="stop",
                tool_uses=None,
            )

        if call_n == 2:
            return ChatResponse(
                content="I will write the file now.",
                model=self.model,
                usage={"input_tokens": 8, "output_tokens": 12},
                finish_reason="tool_use",
                tool_uses=[
                    {
                        "id": "toolu_smoke_001",
                        "name": "Write",
                        "input": {
                            "file_path": str(_FakeProvider._target_file),
                            "content": "integration-ok\n",
                        },
                    }
                ],
            )

        return ChatResponse(
            content="File created successfully.",
            model=self.model,
            usage={"input_tokens": 5, "output_tokens": 8},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream(self, messages, tools=None, **kwargs):
        return iter(())

    def chat_stream_response(self, messages, tools=None, on_text_chunk=None, **kwargs):
        raise NotImplementedError

    _target_file: Path = Path("/tmp/integration_smoke.txt")


class _WriteToolProvider:
    """Provider stub that immediately issues a Write tool call on the first chat."""

    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or "glm-4.5"
        self._calls: int = 0

    def chat(self, messages, tools=None, **kwargs):
        self._calls += 1
        if self._calls == 1:
            return ChatResponse(
                content="Writing file now.",
                model=self.model,
                usage={"input_tokens": 8, "output_tokens": 6},
                finish_reason="tool_use",
                tool_uses=[
                    {
                        "id": "toolu_smoke_write",
                        "name": "Write",
                        "input": {
                            "file_path": str(_WriteToolProvider._target_file),
                            "content": "integration-ok\n",
                        },
                    }
                ],
            )
        return ChatResponse(
            content="File written successfully.",
            model=self.model,
            usage={"input_tokens": 5, "output_tokens": 6},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream(self, messages, tools=None, **kwargs):
        return iter(())

    def chat_stream_response(self, messages, tools=None, on_text_chunk=None, **kwargs):
        raise NotImplementedError

    _target_file: Path = Path("/tmp/integration_smoke.txt")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(home_path: Path, provider: str = "glm") -> None:
    config_dir = home_path / ".clawcodex"
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "default_provider": provider,
        "providers": {
            provider: {
                "api_key": "fake-integration-key",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "default_model": "glm-4.5",
            }
        },
    }
    (config_dir / "config.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIntegrationSmoke:
    """Integration smoke tests — no real network calls."""

    def _make_repl(self, provider_class):
        from src.repl.core import ClawcodexREPL
        return ClawcodexREPL(provider_name="glm", stream=False)

    def test_build_imports(self):
        """App modules import cleanly after WS-1 refactor."""
        import src.cli  # noqa: F401
        import src.repl.core  # noqa: F401
        import src.agent.conversation  # noqa: F401
        import src.types  # noqa: F401
        import src.types.messages  # noqa: F401
        import src.types.content_blocks  # noqa: F401
        import src.types.stream_events  # noqa: F401

    def test_simple_query(self, tmp_path):
        """REPL.chat() with a plain text query returns assistant reply without error."""
        home_path = tmp_path / "home"
        _make_config(home_path)
        old_home = os.environ.get("HOME")
        old_cwd = Path.cwd()
        try:
            os.environ["HOME"] = str(home_path)
            os.chdir(tmp_path)
            with patch("src.repl.core.get_provider_class", return_value=_FakeProvider):
                repl = self._make_repl(_FakeProvider)
                repl.chat("Say hello in one sentence.")
            msgs = repl.session.conversation.get_messages()
            roles = [m["role"] for m in msgs]
            assert "user" in roles
            assert "assistant" in roles
        finally:
            os.environ["HOME"] = old_home or ""
            os.chdir(old_cwd)

    def test_tool_task_write_file(self, tmp_path):
        """REPL.chat() executes a Write tool call and creates the target file."""
        home_path = tmp_path / "home"
        work_path = tmp_path / "work"
        work_path.mkdir()
        _make_config(home_path)

        target = work_path / "integration_smoke.txt"
        _WriteToolProvider._target_file = target

        old_home = os.environ.get("HOME")
        old_cwd = Path.cwd()
        try:
            os.environ["HOME"] = str(home_path)
            os.chdir(work_path)
            with patch("src.repl.core.get_provider_class", return_value=_WriteToolProvider):
                repl = self._make_repl(_WriteToolProvider)
                repl.chat("Create a file named integration_smoke.txt with text integration-ok.")
            assert target.exists(), "Write tool did not create the file"
            assert target.read_text(encoding="utf-8") == "integration-ok\n"
        finally:
            os.environ["HOME"] = old_home or ""
            os.chdir(old_cwd)

    def test_conversation_round_trip(self, tmp_path):
        """Conversation serialises and deserialises correctly after a real chat turn."""
        from src.agent.conversation import Conversation
        from src.types.messages import UserMessage, AssistantMessage

        conv = Conversation()
        conv.add_user_message("Hello")
        conv.add_assistant_message("Hi there!")

        data = conv.to_dict()
        reloaded = Conversation.from_dict(data)

        msgs = reloaded.get_messages()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        content = msgs[1]["content"]
        if isinstance(content, list):
            assert content[0]["text"] == "Hi there!"
        else:
            assert content == "Hi there!"

    def test_message_types_in_api_payload(self):
        """normalize_messages_for_api produces valid Anthropic-style dicts."""
        from src.types.messages import (
            UserMessage, AssistantMessage, normalize_messages_for_api
        )
        from src.types.content_blocks import TextBlock, ToolUseBlock

        msgs = [
            UserMessage(content="ping"),
            AssistantMessage(content=[
                TextBlock(text="pong"),
                ToolUseBlock(id="t1", name="Read", input={"file_path": "/foo"}),
            ]),
        ]
        payload = normalize_messages_for_api(msgs)
        assert payload[0] == {"role": "user", "content": "ping"}
        assert payload[1]["role"] == "assistant"
        blocks = payload[1]["content"]
        assert blocks[0] == {"type": "text", "text": "pong"}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["name"] == "Read"

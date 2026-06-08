"""共享的 FakeProvider — 用于所有稳定性门禁测试。

不发起真实网络请求，所有响应都是确定性的。
"""

from __future__ import annotations

from pathlib import Path
from src.providers.base import ChatResponse


class FakeProvider:
    """Deterministic provider stub — no real API calls."""

    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or "claude-sonnet-4-20250514"
        self._calls: list[list] = []

    def chat(self, messages, tools=None, **kwargs):
        self._calls.append(messages)
        call_n = len(self._calls)

        if call_n == 1:
            return ChatResponse(
                content="Hello from stability gate smoke test.",
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
                        "id": "toolu_stability_001",
                        "name": "Write",
                        "input": {
                            "file_path": str(FakeProvider._target_file),
                            "content": "stability-gate-ok\n",
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

    _target_file: Path = Path("/tmp/stability_gate_smoke.txt")


class WriteToolProvider:
    """Provider stub that immediately issues a Write tool call on the first chat."""

    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or "claude-sonnet-4-20250514"
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
                        "id": "toolu_stability_write",
                        "name": "Write",
                        "input": {
                            "file_path": str(WriteToolProvider._target_file),
                            "content": "stability-gate-ok\n",
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

    _target_file: Path = Path("/tmp/stability_gate_write.txt")

"""Native image input must not advertise or call a second vision model."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.providers.base import ChatResponse
from src.query.query import _call_model_sync
from src.tool_system.context import ToolContext
from src.tool_system.registry import ToolRegistry
from src.tool_system.tools.tool_search import make_tool_search_tool
from src.tool_system.tools.vision_analyze import VisionAnalyzeTool
from src.types.messages import UserMessage


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "gpt-5.4", "gemini-2.5-pro", "new-vision-model"])
@pytest.mark.parametrize("deferred", [False, True])
def test_native_request_gets_pixels_without_vision_tool_or_discovery_hint(model, deferred):
    captured = {}

    def chat(messages, **kwargs):
        captured.update(messages=messages, **kwargs)
        return ChatResponse(content="The image shows a login screen.", model=model,
                            finish_reason="end_turn", usage={})

    provider = SimpleNamespace(model=model, chat=chat)
    tool = replace(VisionAnalyzeTool, should_defer=deferred, is_enabled=lambda: True)
    source = {"type": "base64", "media_type": "image/png", "data": "aW1hZ2U="}
    asyncio.run(_call_model_sync(
        provider=provider, system_prompt="Inspect the attached image.", tools=[tool],
        messages=[UserMessage(content=[
            {"type": "image", "source": source},
            {"type": "text", "text": "what this image is about?"},
        ])],
    ))

    assert "vision_analyze" not in repr(captured["tools"])
    assert "available-deferred-tools" not in repr(captured["messages"])
    user = next(message for message in captured["messages"] if message["role"] == "user")
    assert user["content"][0] == {"type": "image", "source": source}
    assert user["content"][1]["text"] == "what this image is about?"


@pytest.mark.parametrize("query", ["select:vision_analyze", "vision"])
def test_discovery_tracks_the_active_model_after_switching(tmp_path, query):
    tool = replace(VisionAnalyzeTool, should_defer=True, is_enabled=lambda: True)
    search = make_tool_search_tool(ToolRegistry([tool]))
    context = ToolContext(workspace_root=tmp_path)
    context._active_provider = SimpleNamespace(model="claude-sonnet-4-6")

    assert search.call({"query": query}, context).output["matches"] == []
    context._active_provider.model = "deepseek-v4-pro"
    assert search.call({"query": query}, context).output["matches"] == ["vision_analyze"]


def test_text_only_model_keeps_the_configured_tool():
    from src.tool_system.tool_search import filter_tools_for_request

    tool = replace(VisionAnalyzeTool, is_enabled=lambda: True)
    assert filter_tools_for_request([tool], "deepseek-v4-pro") == [tool]


def test_stale_native_tool_call_returns_pixels_without_calling_another_model(tmp_path, monkeypatch):
    from PIL import Image

    ask = Mock(side_effect=AssertionError("native vision must not call a second model"))
    monkeypatch.setattr("src.tool_system.tools.vision_analyze._ask_vision_model", ask)
    path = tmp_path / "screen.png"
    Image.new("RGB", (20, 10)).save(path)
    context = ToolContext(workspace_root=tmp_path)
    context._active_provider = SimpleNamespace(model="claude-sonnet-4-6")

    result = VisionAnalyzeTool.call({"image_url": str(path), "question": "what is shown?"}, context)

    assert not result.is_error
    mapped = VisionAnalyzeTool.map_result_to_api(result.output, "vision-1")
    assert mapped["content"][0]["type"] == "image"
    assert mapped["content"][0]["source"]["media_type"] == "image/png"
    ask.assert_not_called()

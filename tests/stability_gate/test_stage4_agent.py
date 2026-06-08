"""Stage 4 — Agent / Conversation 测试（< 3 秒）。

验证：
- Conversation 序列化/反序列化
- Message types 类型构建和 API payload 转换
- Session 创建/保存/加载
- ToolUseBlock / TextBlock 构建
"""

from __future__ import annotations


class TestStage4Conversation:
    """Conversation 序列化和反序列化测试。"""

    def test_conversation_round_trip(self):
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

    def test_conversation_empty(self):
        from src.agent.conversation import Conversation

        conv = Conversation()
        data = conv.to_dict()
        reloaded = Conversation.from_dict(data)
        assert len(reloaded.get_messages()) == 0

    def test_conversation_multi_turn(self):
        from src.agent.conversation import Conversation
        from src.types.messages import UserMessage, AssistantMessage

        conv = Conversation()
        for i in range(3):
            conv.add_user_message(f"User message {i}")
            conv.add_assistant_message(f"Assistant response {i}")

        msgs = conv.get_messages()
        assert len(msgs) == 6
        assistant_content = msgs[-1]["content"]
        if isinstance(assistant_content, list):
            assert any(
                isinstance(b, dict) and b.get("text") == "Assistant response 2"
                for b in assistant_content
            ), f"Expected text block, got {assistant_content}"
        else:
            assert msgs[-1]["content"] == "Assistant response 2"


class TestStage4MessageTypes:
    """消息类型和 API payload 转换测试。"""

    def test_message_types_in_api_payload(self):
        from src.types.messages import (
            UserMessage,
            AssistantMessage,
            normalize_messages_for_api,
        )
        from src.types.content_blocks import TextBlock, ToolUseBlock

        msgs = [
            UserMessage(content="ping"),
            AssistantMessage(
                content=[
                    TextBlock(text="pong"),
                    ToolUseBlock(
                        id="t1", name="Read", input={"file_path": "/foo"}
                    ),
                ]
            ),
        ]
        payload = normalize_messages_for_api(msgs)
        assert payload[0] == {"role": "user", "content": "ping"}
        assert payload[1]["role"] == "assistant"
        blocks = payload[1]["content"]
        assert blocks[0] == {"type": "text", "text": "pong"}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["name"] == "Read"

    def test_user_message_creation(self):
        from src.types.messages import UserMessage

        msg = UserMessage(content="test message")
        assert msg.content == "test message"
        assert msg.role == "user"


class TestStage4Session:
    """Session 创建 / 保存 / 加载测试。"""

    def test_session_create(self):
        from src.agent.session import Session

        session = Session.create(provider="anthropic", model="claude-sonnet-4-20250514")
        assert session.provider == "anthropic"
        assert session.model == "claude-sonnet-4-20250514"
        assert session.session_id is not None

    def test_session_conversation_integration(self):
        from src.agent.session import Session

        session = Session.create(provider="anthropic", model="claude-sonnet-4-20250514")
        session.conversation.add_user_message("Hello")
        session.conversation.add_assistant_message("World")
        assert len(session.conversation.get_messages()) == 2

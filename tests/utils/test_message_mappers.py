"""Tests for ``src.utils.message_mappers.to_sdk_messages``."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from src.types.messages import (
    AssistantMessage,
    Message,
    ProgressMessage,
    SystemMessage,
    UserMessage,
)
from src.utils.message_mappers import to_sdk_messages


@patch('src.utils.message_mappers.get_session_id', return_value='sess-test')
def test_empty_input_returns_empty_list(_mock: Any) -> None:
    assert to_sdk_messages([]) == []


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_user_message_maps_to_user_sdk_shape(_mock: Any) -> None:
    msg = UserMessage(content='hello', uuid='u-1', timestamp='2026-05-23T00:00:00')
    [out] = to_sdk_messages([msg])
    assert out['type'] == 'user'
    assert out['session_id'] == 'sess-1'
    assert out['parent_tool_use_id'] is None
    assert out['uuid'] == 'u-1'
    assert out['timestamp'] == '2026-05-23T00:00:00'
    assert out['isSynthetic'] is False
    assert out['message'] == {'role': 'user', 'content': 'hello'}


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_user_message_sets_isSynthetic_when_isMeta(_mock: Any) -> None:
    msg = UserMessage(content='nudge', uuid='u-2', isMeta=True)
    [out] = to_sdk_messages([msg])
    assert out['isSynthetic'] is True


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_user_message_sets_isSynthetic_when_isVirtual(_mock: Any) -> None:
    msg = UserMessage(content='hidden', uuid='u-3', isVirtual=True)
    [out] = to_sdk_messages([msg])
    assert out['isSynthetic'] is True


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_user_message_omits_tool_use_result_when_none(_mock: Any) -> None:
    msg = UserMessage(content='hi', uuid='u-4')
    [out] = to_sdk_messages([msg])
    assert 'tool_use_result' not in out


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_user_message_includes_tool_use_result_when_set(_mock: Any) -> None:
    msg = UserMessage(
        content='hi',
        uuid='u-5',
        toolUseResult={'output': 'ok', 'file_uuid': 'f-1'},
    )
    [out] = to_sdk_messages([msg])
    assert out['tool_use_result'] == {'output': 'ok', 'file_uuid': 'f-1'}


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_assistant_message_maps_to_assistant_sdk_shape(_mock: Any) -> None:
    msg = AssistantMessage(content='reply', uuid='a-1')
    [out] = to_sdk_messages([msg])
    assert out['type'] == 'assistant'
    assert out['session_id'] == 'sess-1'
    assert out['parent_tool_use_id'] is None
    assert out['uuid'] == 'a-1'
    # ``message`` must include id/type/role/content for downstream parsers.
    assert out['message']['id'] == 'msg_a-1'
    assert out['message']['type'] == 'message'
    assert out['message']['role'] == 'assistant'
    assert out['message']['content'] == 'reply'
    assert 'error' not in out


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_assistant_message_preserves_model_stop_reason_usage(_mock: Any) -> None:
    """Regression test: id/model/stop_reason/usage must survive the mapping.

    Per CRITIC blocking fix: TS preserves the full ``APIAssistantMessage``
    so Android ``SdkAssistantMessage`` and mobile-apps deserializers can
    parse the wire payload. Previously the Python port stripped these
    fields, producing structurally-thin messages.
    """
    msg = AssistantMessage(
        content='reply',
        uuid='a-2',
        model='claude-opus-4-7',
        stop_reason='end_turn',
        usage={'input_tokens': 10, 'output_tokens': 20},
    )
    [out] = to_sdk_messages([msg])
    inner = out['message']
    assert inner['id'] == 'msg_a-2'
    assert inner['type'] == 'message'
    assert inner['role'] == 'assistant'
    assert inner['model'] == 'claude-opus-4-7'
    assert inner['stop_reason'] == 'end_turn'
    assert inner['usage'] == {'input_tokens': 10, 'output_tokens': 20}


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_assistant_message_omits_optional_fields_when_none(_mock: Any) -> None:
    """Optional fields are elided when ``None`` on the source."""
    msg = AssistantMessage(content='r', uuid='a-3')  # model/stop_reason/usage all None
    [out] = to_sdk_messages([msg])
    inner = out['message']
    assert 'model' not in inner
    assert 'stop_reason' not in inner
    assert 'usage' not in inner


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_assistant_message_includes_error_when_set(_mock: Any) -> None:
    msg = AssistantMessage(
        content='', uuid='a-2', error={'type': 'overloaded'}
    )
    [out] = to_sdk_messages([msg])
    assert out['error'] == {'type': 'overloaded'}


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_assistant_message_with_list_content_serializes_blocks(
    _mock: Any,
) -> None:
    """Content list (e.g. text + tool_use) gets passed through to dict form."""
    msg = AssistantMessage(
        content=[{'type': 'text', 'text': 'hi'}],
        uuid='a-3',
    )
    [out] = to_sdk_messages([msg])
    assert out['message']['role'] == 'assistant'
    assert out['message']['content'] == [{'type': 'text', 'text': 'hi'}]


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_system_compact_boundary_maps_to_system_sdk(_mock: Any) -> None:
    msg = SystemMessage(
        content='Conversation compacted',
        uuid='s-1',
        subtype='compact_boundary',
    )
    [out] = to_sdk_messages([msg])
    assert out == {
        'type': 'system',
        'subtype': 'compact_boundary',
        'session_id': 'sess-1',
        'uuid': 's-1',
    }


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_system_compact_boundary_includes_metadata_when_present(
    _mock: Any,
) -> None:
    """Compact metadata is converted with camelCase→snake_case."""
    meta = {
        'trigger': 'auto',
        'preTokens': 12345,
        'preservedSegment': {
            'headUuid': 'h-1',
            'anchorUuid': 'a-1',
            'tailUuid': 't-1',
        },
    }
    msg = SystemMessage(
        content='compact',
        uuid='s-2',
        subtype='compact_boundary',
    )
    msg.compactMetadata = meta  # type: ignore[attr-defined]
    [out] = to_sdk_messages([msg])
    assert out['compact_metadata'] == {
        'trigger': 'auto',
        'pre_tokens': 12345,
        'preserved_segment': {
            'head_uuid': 'h-1',
            'anchor_uuid': 'a-1',
            'tail_uuid': 't-1',
        },
    }


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_system_local_command_is_deferred(_mock: Any) -> None:
    """The local_command path is deferred per module docstring — dropped."""
    msg = SystemMessage(
        content='<local-command-stdout>hi</local-command-stdout>',
        uuid='s-3',
        subtype='local_command',
    )
    assert to_sdk_messages([msg]) == []


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_progress_message_is_dropped(_mock: Any) -> None:
    """Non-user/assistant/system_compact messages are silently dropped."""
    msg = ProgressMessage(content='', uuid='p-1', toolUseID='t-1')
    assert to_sdk_messages([msg]) == []


@patch('src.utils.message_mappers.get_session_id', return_value='sess-1')
def test_mixed_messages_preserves_order(_mock: Any) -> None:
    msgs: list[Message] = [
        UserMessage(content='1', uuid='u-1'),
        AssistantMessage(content='2', uuid='a-1'),
        UserMessage(content='3', uuid='u-2'),
    ]
    out = to_sdk_messages(msgs)
    assert [m['uuid'] for m in out] == ['u-1', 'a-1', 'u-2']
    assert [m['type'] for m in out] == ['user', 'assistant', 'user']

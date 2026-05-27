"""Prompt module: intro text, attachment build + dedup, formatter."""
from __future__ import annotations

from typing import Any

import pytest

from src.buddy.prompt import (
    build_companion_intro_attachment,
    companion_intro_text,
    format_companion_intro_attachments,
)
from src.types.messages import AttachmentMessage, create_attachment_message


def test_intro_text_contains_name_and_species() -> None:
    text = companion_intro_text('Bytespark', 'duck')
    assert 'Bytespark' in text
    assert 'duck' in text
    assert '# Companion' in text


def test_build_returns_empty_when_no_companion(
    isolated_config: dict[str, Any],
) -> None:
    """No companion in config → []."""
    assert build_companion_intro_attachment([]) == []


def test_build_returns_empty_when_muted(
    isolated_config: dict[str, Any],
) -> None:
    isolated_config['user_id'] = 'fixed-id'
    isolated_config['companion'] = {
        'name': 'Bytespark', 'personality': 'p.', 'hatched_at': 0,
    }
    isolated_config['companion_muted'] = True
    assert build_companion_intro_attachment([]) == []


def test_build_returns_attachment_when_eligible(
    isolated_config: dict[str, Any],
) -> None:
    isolated_config['user_id'] = 'fixed-id'
    isolated_config['companion'] = {
        'name': 'Bytespark', 'personality': 'p.', 'hatched_at': 0,
    }
    result = build_companion_intro_attachment([])
    assert len(result) == 1
    att = result[0]
    assert att['kind'] == 'companion_intro'
    assert att['name'] == 'Bytespark'
    assert 'species' in att


def test_build_dedups_against_marker_in_engine_messages(
    isolated_config: dict[str, Any],
) -> None:
    """When messages already contain an AttachmentMessage for the same
    companion name, build returns []."""
    isolated_config['user_id'] = 'fixed-id'
    isolated_config['companion'] = {
        'name': 'Bytespark', 'personality': 'p.', 'hatched_at': 0,
    }
    marker = create_attachment_message({
        'kind': 'companion_intro',
        'name': 'Bytespark',
        'species': 'duck',
    })
    assert build_companion_intro_attachment([marker]) == []


def test_build_does_not_dedup_against_different_name(
    isolated_config: dict[str, Any],
) -> None:
    isolated_config['user_id'] = 'fixed-id'
    isolated_config['companion'] = {
        'name': 'Bytespark', 'personality': 'p.', 'hatched_at': 0,
    }
    marker = create_attachment_message({
        'kind': 'companion_intro',
        'name': 'OtherName',
        'species': 'duck',
    })
    result = build_companion_intro_attachment([marker])
    assert len(result) == 1


def test_format_emits_system_reminder() -> None:
    out = format_companion_intro_attachments([{
        'kind': 'companion_intro', 'name': 'Bytespark', 'species': 'duck',
    }])
    assert '<system-reminder>' in out
    assert '</system-reminder>' in out
    assert 'Bytespark' in out
    assert 'duck' in out


def test_format_ignores_other_kinds() -> None:
    out = format_companion_intro_attachments([
        {'kind': 'file', 'path': 'foo'},
        {'kind': 'directory', 'path': 'bar/'},
    ])
    assert out == ''


def test_format_empty_input() -> None:
    assert format_companion_intro_attachments([]) == ''


def test_normalize_marker_followed_by_user_does_not_double_user_turn(
    isolated_config: dict[str, Any],
) -> None:
    """Empty-content normalization fragility check (plan §5).

    The dedup marker has ``content=[]``. After ``normalize_messages_for_api``,
    a marker followed by a user message should merge into a single user
    entry — NOT emit two separate user turns. If this test ever breaks,
    the buddy marker shape needs revisiting.
    """
    from src.types.messages import (
        UserMessage, create_user_message, normalize_messages_for_api,
    )
    marker = create_attachment_message({
        'kind': 'companion_intro', 'name': 'X', 'species': 'duck',
    })
    user = create_user_message('hello world')
    out = normalize_messages_for_api([marker, user])
    user_entries = [m for m in out if m.get('role') == 'user']
    # The marker's empty content gets merged with the following user
    # message (predicate at src/types/messages.py:527-535). Expect ONE
    # merged user entry, not two.
    assert len(user_entries) == 1, (
        f"expected 1 merged user entry, got {len(user_entries)}: {out!r}"
    )

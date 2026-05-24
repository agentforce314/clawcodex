"""Tests for ``src.bridge.init_repl_bridge`` (Phase 7 MVP slice)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from src.bridge.init_repl_bridge import (
    TITLE_MAX_LEN,
    InitBridgeOptions,
    derive_title,
    init_repl_bridge,
)


# ── derive_title ────────────────────────────────────────────────────────


def test_derive_title_simple_first_sentence() -> None:
    assert derive_title('Hello world. This is a test.') == 'Hello world.'


def test_derive_title_no_sentence_terminator() -> None:
    """No `.!?` → return the whole text (collapsed/trimmed)."""
    assert derive_title('hello world no period') == 'hello world no period'


def test_derive_title_collapses_whitespace() -> None:
    assert derive_title('hello\n\nworld\t\tagain') == 'hello world again'


def test_derive_title_truncates_at_50_with_ellipsis() -> None:
    text = 'x' * 100
    out = derive_title(text)
    assert out is not None
    assert len(out) == TITLE_MAX_LEN
    assert out.endswith('…')


def test_derive_title_returns_none_for_empty() -> None:
    assert derive_title('') is None
    assert derive_title('   \n\t  ') is None


def test_derive_title_returns_none_for_pure_display_tags() -> None:
    """If content is only display tags, return None to fall through."""
    assert derive_title('<ide_opened_file>foo.py</ide_opened_file>') is None


def test_derive_title_strips_display_tags() -> None:
    """Display tags are stripped before sentence detection."""
    raw = '<ide_opened_file>foo.py</ide_opened_file>Fix the bug.'
    assert derive_title(raw) == 'Fix the bug.'


def test_derive_title_under_max_length_unchanged() -> None:
    assert derive_title('Short prompt.') == 'Short prompt.'


def test_derive_title_question_terminator() -> None:
    assert derive_title('What is X? Then Y.') == 'What is X?'


def test_derive_title_exclamation_terminator() -> None:
    assert derive_title('Wait! That is wrong.') == 'Wait!'


# ── init_repl_bridge — pre-flight failures ──────────────────────────────


def _no_claude_env() -> dict[str, str]:
    return {
        k: v for k, v in os.environ.items()
        if not k.startswith('CLAUDE_AI_')
        and not k.startswith('CLAUDE_BRIDGE_')
    }


@pytest.mark.asyncio
async def test_init_returns_none_when_no_oauth_token() -> None:
    state_log: list[Any] = []
    opts = InitBridgeOptions(
        on_state_change=lambda *a: state_log.append(a),
    )
    with patch.dict(os.environ, _no_claude_env(), clear=True):
        out = await init_repl_bridge(opts)
    assert out is None
    assert any('failed' in str(s) for s in state_log)


@pytest.mark.asyncio
async def test_init_returns_none_when_not_subscriber() -> None:
    """No CLAUDE_AI_OAUTH_ACCESS_TOKEN means is_claude_ai_subscriber → False."""
    env = _no_claude_env() | {'CLAUDE_BRIDGE_OAUTH_TOKEN': 'override-tok'}
    state_log: list[Any] = []
    opts = InitBridgeOptions(
        on_state_change=lambda *a: state_log.append(a),
    )
    with patch.dict(os.environ, env, clear=True):
        out = await init_repl_bridge(opts)
    # We pass the OAuth token override, but is_claude_ai_subscriber
    # checks the claude_ai env vars separately and returns False.
    assert out is None
    assert any('failed' in str(s) for s in state_log)


@pytest.mark.asyncio
async def test_init_returns_none_when_subscriber_but_no_org_uuid() -> None:
    """Has subscriber + profile scope but no org UUID → fail."""
    env = _no_claude_env() | {
        'CLAUDE_BRIDGE_OAUTH_TOKEN': 'override-tok',
        'CLAUDE_AI_OAUTH_ACCESS_TOKEN': 'ai-tok',
        'CLAUDE_AI_OAUTH_SCOPES': 'user:inference user:profile',
        # NO CLAUDE_AI_ORG_UUID
    }
    state_log: list[Any] = []
    opts = InitBridgeOptions(
        on_state_change=lambda *a: state_log.append(a),
    )
    with patch.dict(os.environ, env, clear=True):
        out = await init_repl_bridge(opts)
    assert out is None
    assert any('failed' in str(s) for s in state_log)


# ── init_repl_bridge — v1/v2 delegation ─────────────────────────────────


@pytest.mark.asyncio
async def test_init_delegates_to_env_less_when_subscriber_and_org_uuid() -> None:
    """Happy preflight → routes through init_env_less_bridge_core."""
    env = _no_claude_env() | {
        'CLAUDE_BRIDGE_OAUTH_TOKEN': 'override-tok',
        'CLAUDE_AI_OAUTH_ACCESS_TOKEN': 'ai-tok',
        'CLAUDE_AI_OAUTH_SCOPES': 'user:inference user:profile',
        'CLAUDE_AI_ORG_UUID': 'org-123',
    }
    captured: list[Any] = []

    async def fake_init_env_less(params: Any, **_kw: Any) -> Any:
        captured.append(params)
        return 'fake-handle'

    opts = InitBridgeOptions(initial_name='My session')
    with patch.dict(os.environ, env, clear=True):
        with patch(
            'src.bridge.init_repl_bridge.init_env_less_bridge_core',
            side_effect=fake_init_env_less,
        ):
            out = await init_repl_bridge(opts)
    assert out == 'fake-handle'
    assert len(captured) == 1
    assert captured[0].title == 'My session'
    assert captured[0].org_uuid == 'org-123'
    assert captured[0].initial_history_cap == 200


@pytest.mark.asyncio
async def test_init_uses_default_title_when_none() -> None:
    env = _no_claude_env() | {
        'CLAUDE_BRIDGE_OAUTH_TOKEN': 'override-tok',
        'CLAUDE_AI_OAUTH_ACCESS_TOKEN': 'ai-tok',
        'CLAUDE_AI_OAUTH_SCOPES': 'user:inference user:profile',
        'CLAUDE_AI_ORG_UUID': 'org-123',
    }
    captured: list[Any] = []

    async def fake_init(params: Any, **_kw: Any) -> Any:
        captured.append(params)
        return 'h'

    opts = InitBridgeOptions()
    with patch.dict(os.environ, env, clear=True):
        with patch(
            'src.bridge.init_repl_bridge.init_env_less_bridge_core',
            side_effect=fake_init,
        ):
            await init_repl_bridge(opts)
    assert captured[0].title == 'Remote Control session'


@pytest.mark.asyncio
async def test_init_v1_path_fails_without_callbacks() -> None:
    """Perpetual=True forces v1; missing create_session callback → fail."""
    env = _no_claude_env() | {
        'CLAUDE_BRIDGE_OAUTH_TOKEN': 'override-tok',
        'CLAUDE_AI_OAUTH_ACCESS_TOKEN': 'ai-tok',
        'CLAUDE_AI_OAUTH_SCOPES': 'user:inference user:profile',
        'CLAUDE_AI_ORG_UUID': 'org-123',
    }
    state_log: list[Any] = []
    opts = InitBridgeOptions(
        perpetual=True,
        on_state_change=lambda *a: state_log.append(a),
    )
    with patch.dict(os.environ, env, clear=True):
        out = await init_repl_bridge(opts)
    assert out is None
    assert any('failed' in str(s) for s in state_log)


@pytest.mark.asyncio
async def test_init_passes_through_callbacks_to_env_less() -> None:
    """Optional callbacks land on the EnvLessBridgeParams."""
    env = _no_claude_env() | {
        'CLAUDE_BRIDGE_OAUTH_TOKEN': 'override-tok',
        'CLAUDE_AI_OAUTH_ACCESS_TOKEN': 'ai-tok',
        'CLAUDE_AI_OAUTH_SCOPES': 'user:inference user:profile',
        'CLAUDE_AI_ORG_UUID': 'org-123',
    }
    captured: list[Any] = []

    async def fake_init(params: Any, **_kw: Any) -> Any:
        captured.append(params)
        return 'h'

    def on_inbound(_msg: Any) -> None:
        pass

    def on_interrupt() -> None:
        pass

    opts = InitBridgeOptions(
        on_inbound_message=on_inbound,
        on_interrupt=on_interrupt,
        tags=['ccr-mirror'],
        outbound_only=True,
    )
    with patch.dict(os.environ, env, clear=True):
        with patch(
            'src.bridge.init_repl_bridge.init_env_less_bridge_core',
            side_effect=fake_init,
        ):
            await init_repl_bridge(opts)
    p = captured[0]
    assert p.on_inbound_message is on_inbound
    assert p.on_interrupt is on_interrupt
    assert p.tags == ['ccr-mirror']
    assert p.outbound_only is True

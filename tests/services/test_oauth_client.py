"""Tests for ``src.services.oauth.client``."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.auth.claude_ai import ENV_ORG_UUID
from src.services.oauth.client import get_organization_uuid


def _no_claude_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_AI_')}


@pytest.mark.asyncio
async def test_get_organization_uuid_none_when_unset() -> None:
    with patch.dict(os.environ, _no_claude_env(), clear=True):
        assert await get_organization_uuid() is None


@pytest.mark.asyncio
async def test_get_organization_uuid_returns_env_value() -> None:
    env = _no_claude_env() | {ENV_ORG_UUID: 'org-abc'}
    with patch.dict(os.environ, env, clear=True):
        assert await get_organization_uuid() == 'org-abc'

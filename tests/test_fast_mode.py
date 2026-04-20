"""Tests for R2-WS-9: Fast mode system."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.utils.fast_mode import (
    FAST_MODE_MODEL,
    FastModeState,
    get_fast_mode_model,
    is_fast_mode_enabled,
)


class TestFastModeState:
    def test_default_disabled(self):
        state = FastModeState()
        assert state.is_enabled is False

    def test_enable(self):
        state = FastModeState()
        state.enable()
        assert state.is_enabled is True

    def test_disable(self):
        state = FastModeState()
        state.enable()
        state.disable()
        assert state.is_enabled is False

    def test_reset(self):
        state = FastModeState()
        state.enable()
        state.reset()
        assert state.is_enabled is False

    def test_initial_enabled(self):
        state = FastModeState(_enabled=True)
        assert state.is_enabled is True


class TestIsFastModeEnabled:
    def test_session_state_priority(self):
        state = FastModeState()
        state.enable()
        assert is_fast_mode_enabled(config_value=False, session_state=state) is True

    def test_env_override(self):
        with patch.dict(os.environ, {"CLAUDE_FAST_MODE": "true"}):
            assert is_fast_mode_enabled() is True

    def test_env_false(self):
        with patch.dict(os.environ, {"CLAUDE_FAST_MODE": "false"}):
            assert is_fast_mode_enabled() is False

    def test_config_value(self):
        with patch.dict(os.environ, {}, clear=False):
            env = dict(os.environ)
            env.pop("CLAUDE_FAST_MODE", None)
            with patch.dict(os.environ, env, clear=True):
                assert is_fast_mode_enabled(config_value=True) is True

    def test_default_false(self):
        with patch.dict(os.environ, {}, clear=True):
            assert is_fast_mode_enabled() is False


class TestGetFastModeModel:
    def test_default_model(self):
        with patch.dict(os.environ, {}, clear=False):
            env = dict(os.environ)
            env.pop("CLAUDE_FAST_MODE_MODEL", None)
            with patch.dict(os.environ, env, clear=True):
                assert get_fast_mode_model() == FAST_MODE_MODEL

    def test_env_override(self):
        with patch.dict(os.environ, {"CLAUDE_FAST_MODE_MODEL": "custom-fast-model"}):
            assert get_fast_mode_model() == "custom-fast-model"

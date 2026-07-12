"""Context-window sizes for 1M-context models (deepseek-v4, glm-5.2).

Regression guard for the agent-server status bar reporting 200K for models that
ship a 1M window. The display reads ``context_analyzer``; autocompact reads the
canonical registry (``src/models/configs.py``) — both must agree.
"""

from src.context_system.context_analyzer import (
    get_context_window_for_model as display_window,
)
from src.models.context import get_context_window_for_model as canonical_window


def test_deepseek_v4_is_1m_in_display_and_canonical():
    assert display_window("deepseek-v4-pro") == 1_000_000
    assert canonical_window("deepseek-v4-pro") == 1_000_000


def test_glm_5_2_is_1m_in_display_and_canonical():
    assert display_window("glm-5.2") == 1_000_000
    assert canonical_window("glm-5.2") == 1_000_000


def test_glm_4_legacy_not_promoted_to_1m():
    # glm-4 must not prefix-match glm-5.2's 1M window (both are exact keys).
    assert display_window("glm-4") == 128_000
    assert canonical_window("glm-4") == 128_000


def test_minimax_current_context_windows():
    assert display_window("MiniMax-M3") == 1_000_000
    assert canonical_window("MiniMax-M3") == 1_000_000
    assert display_window("MiniMax-M2.7") == 204_800
    assert canonical_window("MiniMax-M2.7") == 204_800


def test_legacy_deepseek_chat_not_promoted_to_1m():
    # Only deepseek-v4* gets 1M; legacy deepseek-chat/-reasoner do not.
    assert display_window("deepseek-chat") != 1_000_000


def test_display_defers_to_canonical_for_registered_models():
    # A model registered in the canonical table is reflected in the display via
    # the registry fallback, so the two never drift again.
    assert display_window("deepseek-v4-pro") == canonical_window("deepseek-v4-pro")

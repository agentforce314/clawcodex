"""Fast mode system matching TypeScript utils/fastMode.ts."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


FAST_MODE_MODEL = "claude-3-5-haiku-20241022"


@dataclass
class FastModeState:
    """Track fast mode on/off per session."""
    _enabled: bool = False
    _session_override: bool | None = None

    def enable(self) -> None:
        self._session_override = True

    def disable(self) -> None:
        self._session_override = False

    def reset(self) -> None:
        self._session_override = None

    @property
    def is_enabled(self) -> bool:
        if self._session_override is not None:
            return self._session_override
        return self._enabled


def is_fast_mode_enabled(
    *,
    config_value: bool | None = None,
    env_override: bool | None = None,
    session_state: FastModeState | None = None,
) -> bool:
    """Check if fast mode is enabled from config, env, or session state.

    Priority: session_state > env_override > config_value.

    Side effect (WI-2.1): on first ``True`` result, latch
    ``fast_mode_header_latched`` so subsequent ``cache_control`` emissions
    avoid mid-session toggles busting the prompt cache. The latch is
    sticky-on; once set it stays True for the session even if fast mode
    is later disabled via ``FastModeState.disable()``.
    """
    enabled = _resolve_fast_mode_enabled(
        config_value=config_value,
        env_override=env_override,
        session_state=session_state,
    )
    if enabled:
        # Sticky-on: latch the header field so we never bust cache on
        # mid-session toggles. The latch lives in src/state/cache_state.py;
        # late-import to avoid a top-level circular dependency between
        # state/cache_state.py and src.providers (used by is_first_party_provider).
        from src.state.cache_state import get_beta_header_latches
        latches = get_beta_header_latches()
        if not latches.fast_mode_header_latched:
            latches.fast_mode_header_latched = True
    return enabled


def _resolve_fast_mode_enabled(
    *,
    config_value: bool | None,
    env_override: bool | None,
    session_state: FastModeState | None,
) -> bool:
    """Pure-function half of is_fast_mode_enabled (no latch side effect).

    Extracted so tests of the latch behavior can call it independently of
    the latch wiring, and so the latch wiring is concentrated in one place
    (the public ``is_fast_mode_enabled``).
    """
    if session_state is not None and session_state._session_override is not None:
        return session_state._session_override

    if env_override is not None:
        return env_override

    env_val = os.environ.get("CLAUDE_FAST_MODE", "").lower()
    if env_val in ("1", "true", "yes"):
        return True
    if env_val in ("0", "false", "no"):
        return False

    if config_value is not None:
        return config_value

    return False


def get_fast_mode_model() -> str:
    """Get the model to use in fast mode."""
    return os.environ.get("CLAUDE_FAST_MODE_MODEL", FAST_MODE_MODEL)

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
    """
    # Session state takes priority
    if session_state is not None and session_state._session_override is not None:
        return session_state._session_override

    # Env override
    if env_override is not None:
        return env_override

    env_val = os.environ.get("CLAUDE_FAST_MODE", "").lower()
    if env_val in ("1", "true", "yes"):
        return True
    if env_val in ("0", "false", "no"):
        return False

    # Config value
    if config_value is not None:
        return config_value

    return False


def get_fast_mode_model() -> str:
    """Get the model to use in fast mode."""
    return os.environ.get("CLAUDE_FAST_MODE_MODEL", FAST_MODE_MODEL)

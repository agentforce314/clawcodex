"""Bridge SDK stub (NOT the CCR bridge; see ``src/bridge/`` for that work).

DEPRECATED. The naming collides with ``src/bridge/``, which is the CCR
bridge implementation tracked in
``my-docs/ch16-remote-refactoring-plan.md``. New code should NOT import
from here; existing tests in ``tests/test_bridge.py`` keep working.

The deprecation warning below fires on import. ``pyproject.toml``'s
``[tool.pytest.ini_options].filterwarnings`` suppresses it during the
existing test suite so ``pytest -W error::DeprecationWarning`` does not
break unrelated runs.
"""
from __future__ import annotations

import warnings

warnings.warn(
    'src.services.bridge is deprecated; use src.bridge for CCR remote-execution.',
    DeprecationWarning,
    stacklevel=2,
)

from clawcodex_ext.services.bridge.session import BridgeSession, BridgeSessionConfig, BridgeSessionState
from clawcodex_ext.services.bridge.transport import BridgeTransport, WebSocketTransport
from clawcodex_ext.services.bridge.auth import BridgeAuth, BridgeToken

__all__ = [
    "BridgeAuth",
    "BridgeSession",
    "BridgeSessionConfig",
    "BridgeSessionState",
    "BridgeToken",
    "BridgeTransport",
    "WebSocketTransport",
]

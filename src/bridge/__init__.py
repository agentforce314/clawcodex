"""CCR bridge subsystem (Bridge v1 + Bridge v2 + shared primitives).

Real implementations land here per ``my-docs/get-parity-by-folder/
bridge-refactoring-plan.md``. The ``ARCHIVE_NAME``/``MODULE_COUNT``/
``SAMPLE_FILES``/``PORTING_NOTE`` re-exports are preserved for backwards
compat with ``tests/test_porting_workspace.py:73-79`` (``from src import
bridge`` then ``bridge.MODULE_COUNT > 0``).

This module also re-exports the most-used Phase 1 leaves so consumers can
write ``from src.bridge import BoundedUUIDSet`` rather than the full path.
Per refactoring plan §4 risk row, every re-export must be importable
cleanly with no Phase 2 module present — verified by the legacy
test_porting_workspace test which would otherwise fail at import time.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'reference_data' / 'subsystems' / 'bridge.json'
_SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())

ARCHIVE_NAME = _SNAPSHOT['archive_name']
MODULE_COUNT = _SNAPSHOT['module_count']
SAMPLE_FILES = tuple(_SNAPSHOT['sample_files'])
PORTING_NOTE = f"Python placeholder package for '{ARCHIVE_NAME}' with {MODULE_COUNT} archived module references."

# -----------------------------------------------------------------------------
# Phase 1 re-exports (Phase 2 deps not required for any of these imports)
# -----------------------------------------------------------------------------

from src.bridge.bounded_uuid_set import BoundedUUIDSet
from src.bridge.bridge_permission_callbacks import (
    BridgePermissionResponse,
    is_bridge_permission_response,
)
from src.bridge.capacity_wake import CapacityWake, create_capacity_wake
from src.bridge.close_codes import (
    WS_CLOSE_EPOCH_MISMATCH,
    WS_CLOSE_INIT_FAILURE,
    WS_CLOSE_PERMANENT_UNAUTHORIZED,
    WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED,
    WS_CLOSE_SESSION_NOT_FOUND,
)
from src.bridge.exceptions import (
    BridgeAuthError,
    BridgeFatalError,
    EpochSupersededError,
)
from src.bridge.flush_gate import FlushGate
from src.bridge.inbound_messages import (
    extract_inbound_message_fields,
    normalize_image_blocks,
)
from src.bridge.poll_config_defaults import (
    DEFAULT_POLL_CONFIG,
    PollIntervalConfig,
)
from src.bridge.session_id_compat import (
    set_cse_shim_gate,
    to_compat_session_id,
    to_infra_session_id,
)
from src.bridge.types import (
    BRIDGE_LOGIN_ERROR,
    BRIDGE_LOGIN_INSTRUCTION,
    DEFAULT_SESSION_TIMEOUT_MS,
    REMOTE_CONTROL_DISCONNECTED_MSG,
    BridgeConfig,
    SessionActivity,
)
from src.bridge.work_secret import (
    WorkSecret,
    build_ccr_v2_sdk_url,
    build_sdk_url,
    decode_work_secret,
    same_session_id,
)

__all__ = [
    # Legacy archive metadata (test_porting_workspace.py compat)
    'ARCHIVE_NAME',
    'MODULE_COUNT',
    'PORTING_NOTE',
    'SAMPLE_FILES',
    # Phase 1 leaves
    'BRIDGE_LOGIN_ERROR',
    'BRIDGE_LOGIN_INSTRUCTION',
    'BoundedUUIDSet',
    'BridgeAuthError',
    'BridgeConfig',
    'BridgeFatalError',
    'BridgePermissionResponse',
    'CapacityWake',
    'DEFAULT_POLL_CONFIG',
    'DEFAULT_SESSION_TIMEOUT_MS',
    'EpochSupersededError',
    'FlushGate',
    'PollIntervalConfig',
    'REMOTE_CONTROL_DISCONNECTED_MSG',
    'SessionActivity',
    'WS_CLOSE_EPOCH_MISMATCH',
    'WS_CLOSE_INIT_FAILURE',
    'WS_CLOSE_PERMANENT_UNAUTHORIZED',
    'WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED',
    'WS_CLOSE_SESSION_NOT_FOUND',
    'WorkSecret',
    'build_ccr_v2_sdk_url',
    'build_sdk_url',
    'create_capacity_wake',
    'decode_work_secret',
    'extract_inbound_message_fields',
    'is_bridge_permission_response',
    'normalize_image_blocks',
    'same_session_id',
    'set_cse_shim_gate',
    'to_compat_session_id',
    'to_infra_session_id',
]

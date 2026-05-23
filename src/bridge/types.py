"""Bridge subsystem types.

Ports ``typescript/src/bridge/types.ts``. Consolidated type module
containing dataclasses, TypedDicts, Protocols, and module-level constants
shared across the bridge subsystem. The TS file mixes wire-format types
(``WorkData``, ``WorkResponse``) with dependency-injection Protocols
(``BridgeApiClient``, ``SessionHandle``, ``SessionSpawner``, ``BridgeLogger``);
Python keeps the same arrangement so callers see one type module per TS file.

For wire-level message types (``SDKMessage``, ``SDKControlRequest`` etc.) see
``src.bridge.sdk_types`` — those live separately because they cross multiple
TS files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Literal,
    Protocol,
    TypedDict,
    Union,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_SESSION_TIMEOUT_MS: int = 24 * 60 * 60 * 1000
"""Default per-session timeout (24 hours).

Mirrors TS ``DEFAULT_SESSION_TIMEOUT_MS`` on ``types.ts:2``.
"""

BRIDGE_LOGIN_INSTRUCTION: str = (
    'Remote Control is only available with claude.ai subscriptions. '
    'Please use `/login` to sign in with your claude.ai account.'
)
"""Reusable login guidance appended to bridge auth errors.

Mirrors TS ``BRIDGE_LOGIN_INSTRUCTION`` on ``types.ts:5-6``.
"""

BRIDGE_LOGIN_ERROR: str = (
    'Error: You must be logged in to use Remote Control.\n\n'
    + BRIDGE_LOGIN_INSTRUCTION
)
"""Full error printed when ``claude remote-control`` is run without auth.

Mirrors TS ``BRIDGE_LOGIN_ERROR`` on ``types.ts:9-11``.
"""

REMOTE_CONTROL_DISCONNECTED_MSG: str = 'Remote Control disconnected.'
"""Shown when the user disconnects Remote Control via /remote-control or
ultraplan launch.

Mirrors TS ``REMOTE_CONTROL_DISCONNECTED_MSG`` on ``types.ts:14``.
"""


# ---------------------------------------------------------------------------
# Wire protocol types (environments API)
# ---------------------------------------------------------------------------


WorkDataType = Literal['session', 'healthcheck']


class WorkData(TypedDict):
    """Inner data payload of a work poll response.

    Mirrors TS ``WorkData`` on ``types.ts:18-21``.
    """

    type: WorkDataType
    id: str


class WorkResponse(TypedDict):
    """Top-level work poll response from the environments API.

    Mirrors TS ``WorkResponse`` on ``types.ts:23-31``. ``secret`` is base64url-
    encoded JSON (a ``WorkSecret``); use ``decode_work_secret`` to parse it.
    """

    id: str
    type: Literal['work']
    environment_id: str
    state: str
    data: WorkData
    secret: str
    created_at: str


SessionDoneStatus = Literal['completed', 'failed', 'interrupted']
"""Mirrors TS ``SessionDoneStatus`` on ``types.ts:53``."""

SessionActivityType = Literal['tool_start', 'text', 'result', 'error']
"""Mirrors TS ``SessionActivityType`` on ``types.ts:55``."""


@dataclass(frozen=True)
class SessionActivity:
    """A single activity event seen on the child's NDJSON stream.

    Mirrors TS ``SessionActivity`` on ``types.ts:57-61``. Used in the ring
    buffer maintained by ``SessionRunner``.
    """

    type: SessionActivityType
    summary: str
    timestamp: float


SpawnMode = Literal['single-session', 'worktree', 'same-dir']
"""How ``claude remote-control`` chooses session working directories.

Mirrors TS ``SpawnMode`` on ``types.ts:69``:
- ``single-session``: one session in cwd, bridge tears down when it ends
- ``worktree``: persistent server, every session gets an isolated git worktree
- ``same-dir``: persistent server, every session shares cwd
"""


BridgeWorkerType = Literal['claude_code', 'claude_code_assistant']
"""Well-known ``worker_type`` values this codebase produces.

Mirrors TS ``BridgeWorkerType`` on ``types.ts:79``. The backend treats this
field as an opaque string; this narrow type is for internal exhaustiveness.
"""


@dataclass
class BridgeConfig:
    """Configuration for a single bridge instance.

    Mirrors TS ``BridgeConfig`` on ``types.ts:81-115``. Mutable because
    ``spawn_mode`` can change at runtime when the user presses ``w`` to
    toggle between same-dir and worktree mode (see ``bridgeMain.ts``).
    """

    dir: str
    machine_name: str
    branch: str
    git_repo_url: str | None
    max_sessions: int
    spawn_mode: SpawnMode
    verbose: bool
    sandbox: bool
    bridge_id: str
    worker_type: str
    environment_id: str
    api_base_url: str
    session_ingress_url: str
    reuse_environment_id: str | None = None
    debug_file: str | None = None
    session_timeout_ms: int | None = None


# ---------------------------------------------------------------------------
# Permission response event (control_response wrapper)
# ---------------------------------------------------------------------------


class _PermissionResponseInner(TypedDict):
    subtype: Literal['success']
    request_id: str
    response: dict[str, Any]


class PermissionResponseEvent(TypedDict):
    """A ``control_response`` event sent back to a session via the events API.

    Mirrors TS ``PermissionResponseEvent`` on ``types.ts:124-131``.
    """

    type: Literal['control_response']
    response: _PermissionResponseInner


# ---------------------------------------------------------------------------
# Dependency-injection Protocols
# ---------------------------------------------------------------------------


class BridgeApiClient(Protocol):
    """HTTP client surface for the environments API.

    Mirrors TS ``BridgeApiClient`` on ``types.ts:133-176``. Concrete
    implementation lands in Phase 3 (``src/bridge/bridge_api.py``); this
    Protocol exists so orchestrators (Phase 5+) can be ported and unit-tested
    against fakes before the real client is built.
    """

    async def register_bridge_environment(
        self, config: BridgeConfig
    ) -> dict[str, str]:
        """POST /v1/environments/bridge. Returns ``{environment_id, environment_secret}``."""
        ...

    async def poll_for_work(
        self,
        environment_id: str,
        environment_secret: str,
        cancel_event: Any | None = None,
        reclaim_older_than_ms: int | None = None,
    ) -> WorkResponse | None:
        """GET .../work/poll. Returns ``None`` when no work is queued."""
        ...

    async def acknowledge_work(
        self, environment_id: str, work_id: str, session_token: str
    ) -> None:
        """POST .../work/{workId}/ack."""
        ...

    async def stop_work(
        self, environment_id: str, work_id: str, force: bool
    ) -> None:
        """POST .../work/{workId}/stop."""
        ...

    async def deregister_environment(self, environment_id: str) -> None:
        """DELETE /v1/environments/bridge/{environmentId}."""
        ...

    async def send_permission_response_event(
        self,
        session_id: str,
        event: PermissionResponseEvent,
        session_token: str,
    ) -> None:
        """POST /v1/sessions/{sessionId}/events."""
        ...

    async def archive_session(self, session_id: str) -> None:
        """POST /v1/sessions/{sessionId}/archive."""
        ...

    async def reconnect_session(
        self, environment_id: str, session_id: str
    ) -> None:
        """POST .../bridge/reconnect — force-stop stale workers + re-queue."""
        ...

    async def heartbeat_work(
        self, environment_id: str, work_id: str, session_token: str
    ) -> dict[str, Any]:
        """POST .../work/{workId}/heartbeat. Returns ``{lease_extended, state}``."""
        ...


class SessionHandle(Protocol):
    """Handle returned by a session spawner for one running child CLI.

    Mirrors TS ``SessionHandle`` on ``types.ts:178-190``. The TS version has
    a ``done: Promise<SessionDoneStatus>``; Python uses ``asyncio.Future`` or
    ``asyncio.Task`` — Protocol method signatures express it as
    ``async def wait_done()``.
    """

    @property
    def session_id(self) -> str: ...

    @property
    def access_token(self) -> str: ...

    @property
    def activities(self) -> list[SessionActivity]:
        """Ring buffer of recent activities (last ~10)."""
        ...

    @property
    def current_activity(self) -> SessionActivity | None: ...

    @property
    def last_stderr(self) -> list[str]:
        """Ring buffer of last stderr lines."""
        ...

    async def wait_done(self) -> SessionDoneStatus:
        """Block until the child exits and return the final status."""
        ...

    def kill(self) -> None:
        """SIGTERM the child (graceful)."""
        ...

    def force_kill(self) -> None:
        """SIGKILL the child (immediate)."""
        ...

    def write_stdin(self, data: str) -> None:
        """Write directly to child stdin."""
        ...

    def update_access_token(self, token: str) -> None:
        """Update the access token (e.g. after refresh)."""
        ...


class SessionSpawnOpts(TypedDict, total=False):
    """Spawn-time options for one session.

    Mirrors TS ``SessionSpawnOpts`` on ``types.ts:192-207``. ``use_ccr_v2``
    + ``worker_epoch`` are required together for the v2 transport path;
    ``on_first_user_message`` is fired once on first real user prompt for
    title derivation.
    """

    session_id: str  # required
    sdk_url: str  # required
    access_token: str  # required
    use_ccr_v2: bool
    worker_epoch: int
    on_first_user_message: Callable[[str], None]


class SessionSpawner(Protocol):
    """Factory for child CLI processes.

    Mirrors TS ``SessionSpawner`` on ``types.ts:209-211``.
    """

    def spawn(self, opts: SessionSpawnOpts, working_dir: str) -> SessionHandle: ...


class BridgeLogger(Protocol):
    """Status / activity logger for the bridge.

    Mirrors TS ``BridgeLogger`` on ``types.ts:213-262``. The TS surface is
    sprawling (17+ methods) because it owns terminal rendering. Phase 9
    delivers the concrete implementation; Phase 1 just establishes the
    Protocol so orchestrators can be wired against a fake/no-op logger.
    """

    def print_banner(
        self, config: BridgeConfig, environment_id: str
    ) -> None: ...

    def log_session_start(self, session_id: str, prompt: str) -> None: ...

    def log_session_complete(
        self, session_id: str, duration_ms: float
    ) -> None: ...

    def log_session_failed(self, session_id: str, error: str) -> None: ...

    def log_status(self, message: str) -> None: ...

    def log_verbose(self, message: str) -> None: ...

    def log_error(self, message: str) -> None: ...

    def log_reconnected(self, disconnected_ms: float) -> None: ...

    def update_idle_status(self) -> None: ...

    def update_reconnecting_status(
        self, delay_str: str, elapsed_str: str
    ) -> None: ...

    def update_session_status(
        self,
        session_id: str,
        elapsed: str,
        activity: SessionActivity,
        trail: list[str],
    ) -> None: ...

    def clear_status(self) -> None: ...

    def set_repo_info(self, repo_name: str, branch: str) -> None: ...

    def set_debug_log_path(self, path: str) -> None: ...

    def set_attached(self, session_id: str) -> None: ...

    def update_failed_status(self, error: str) -> None: ...

    def toggle_qr(self) -> None: ...

    def update_session_count(
        self, active: int, max_sessions: int, mode: SpawnMode
    ) -> None: ...

    def set_spawn_mode_display(
        self, mode: Literal['same-dir', 'worktree'] | None
    ) -> None: ...

    def add_session(self, session_id: str, url: str) -> None: ...

    def update_session_activity(
        self, session_id: str, activity: SessionActivity
    ) -> None: ...

    def set_session_title(self, session_id: str, title: str) -> None: ...

    def remove_session(self, session_id: str) -> None: ...

    def refresh_display(self) -> None: ...


# ---------------------------------------------------------------------------
# ReplBridgeHandle Protocol (referenced by repl_bridge_handle.py)
# ---------------------------------------------------------------------------


class ReplBridgeHandle(Protocol):
    """Opaque handle returned by ``init_bridge_core`` / ``init_env_less_bridge_core``.

    Mirrors the consumer-facing surface of TS ``ReplBridgeHandle``
    (``replBridge.ts`` and ``remoteBridgeCore.ts``). Implementations land in
    Phase 5 (env-less) and Phase 6 (env-based); the Protocol exists in
    Phase 1 so ``repl_bridge_handle.py`` (process-global pointer) and other
    consumers can be ported now.

    ``bridge_session_id`` is exposed for compat-ID derivation in
    ``repl_bridge_handle.get_self_bridge_compat_id()``.
    """

    @property
    def bridge_session_id(self) -> str: ...

    @property
    def environment_id(self) -> str: ...

    @property
    def session_ingress_url(self) -> str: ...

    async def write_messages(self, messages: list[Any]) -> None: ...

    async def write_sdk_messages(self, messages: list[Any]) -> None: ...

    async def send_control_request(
        self, request_id: str, request: dict[str, Any]
    ) -> None: ...

    async def send_control_response(
        self, request_id: str, response: dict[str, Any]
    ) -> None: ...

    async def send_cancel_request(self, request_id: str) -> None: ...

    async def send_result(self) -> None: ...

    async def teardown(self) -> None: ...


# ---------------------------------------------------------------------------
# Internal aliases for type-checking
# ---------------------------------------------------------------------------


GetAccessToken = Callable[[], Union[str, None, Awaitable[Union[str, None]]]]
"""Sync-or-async access-token getter used widely across the bridge.

Mirrors the TS pattern of ``() => string | undefined | Promise<...>``.
"""

OnAuth401 = Callable[[str | None], Awaitable[bool]]
"""Async callback invoked on a 401. Returns True if token refresh succeeded.

Mirrors TS ``onAuth401`` callbacks scattered across ``bridgeApi.ts``,
``remoteBridgeCore.ts``, and ``replBridge.ts``.
"""


__all__ = [
    'BRIDGE_LOGIN_ERROR',
    'BRIDGE_LOGIN_INSTRUCTION',
    'BridgeApiClient',
    'BridgeConfig',
    'BridgeLogger',
    'BridgeWorkerType',
    'DEFAULT_SESSION_TIMEOUT_MS',
    'GetAccessToken',
    'OnAuth401',
    'PermissionResponseEvent',
    'REMOTE_CONTROL_DISCONNECTED_MSG',
    'ReplBridgeHandle',
    'SessionActivity',
    'SessionActivityType',
    'SessionDoneStatus',
    'SessionHandle',
    'SessionSpawnOpts',
    'SessionSpawner',
    'SpawnMode',
    'WorkData',
    'WorkDataType',
    'WorkResponse',
]

# Re-affirm WorkDataType is exported (Literal alias — appears in WorkData).
# Listed above explicitly so downstream consumers can write
# ``from src.bridge.types import WorkDataType``.

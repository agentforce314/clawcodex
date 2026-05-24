"""Env-based bridge orchestrator — Phase 6 MVP slice.

Ports the **public surface + happy path** of
``typescript/src/bridge/replBridge.ts`` (~2400 lines in TS).

**Scope decision**: A full Phase 6 port (perpetual mode, dual v1/v2
transport, multi-attempt env recreation on 404, crash-recovery pointer
integration, dropped-batch telemetry, deterministic poll-loop backoff,
work-id dedup across stale redeliveries, etc.) is 2-3 weeks per the
refactoring plan. For autonomous porting in one session, this module
implements the structural skeleton + single-session happy path:

* Register environment → create session
* Work-poll loop (basic, v2-only transport)
* Spawn session via Phase 4 ``session_runner``
* ``ReplBridgeHandle`` surface — write_messages / control / teardown
* Teardown — stop_work + archive + deregister

What is **explicitly deferred** (with TODOs at the call sites):

* **v1 transport** (``HybridTransport`` POST writes + WS reads) — v2 is
  the going-forward path; v1 is being deprecated server-side. Module
  raises ``NotImplementedError`` if work secrets indicate v1 only.
* **Perpetual mode** (crash-recovery pointer integration, env reuse via
  ``reuseEnvironmentId``). Caller must set ``perpetual=False``.
* **Env recreation** (the Strategy-1 / Strategy-2 reconnect dance after
  a poll 404). Module logs the 404, fires ``on_state_change('failed')``,
  and exits the poll loop. Phase 8 ``bridgeMain`` is the right place to
  build the full recreation flow.
* **JWT refresh integration with the spawned session** — the
  ``TokenRefreshScheduler`` exists; wiring it to ``session.update_access_token``
  on refresh is left to a follow-up. For now sessions use their initial
  JWT until expiry.
* **Multi-session** — the MVP handles one session at a time; second poll
  result is rejected. Phase 8 ``bridgeMain`` handles the multi-session
  daemon case via spawn-mode dispatch.
* **Backoff/give-up logic** — the poll loop uses a fixed interval from
  the config. The full TS backoff machinery (two-track error counters,
  process-suspension detection, 10-min give-up) lands in Phase 8.
* **Dropped-batch telemetry** + **work-id completion dedup** — both
  log-only enhancements; deferred.

What IS ported in full:

* Public types: ``ReplBridgeHandle``, ``BridgeState``, ``BridgeCoreParams``
* ``init_bridge_core(params, *, http_client?, api_client?, spawner?)`` — the factory
* Single-session lifecycle: register → poll → spawn → done → archive
* Idempotent teardown
* OAuth + env-secret auth via ``bridge_api``

This is sufficient to validate the bridge_api + session_runner + v2
transport integration end-to-end. Phase 8 will fill in the multi-
session + reconnect + perpetual surface.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.bridge.bridge_api import (
    BridgeFatalError,
    create_bridge_api_client,
    is_expired_error_type,
)
from src.bridge.jwt_utils import TokenRefreshScheduler
from src.bridge.poll_config_defaults import (
    DEFAULT_POLL_CONFIG,
    PollIntervalConfig,
)
from src.bridge.session_id_compat import to_compat_session_id
from src.bridge.session_runner import (
    PermissionRequest,
    SessionSpawnerDeps,
    create_session_spawner,
)
from src.bridge.types import (
    BridgeApiClient,
    BridgeConfig,
    SessionActivity,
    SessionHandle,
    SessionSpawnOpts,
    SessionSpawner,
)
from src.bridge.work_secret import (
    build_ccr_v2_sdk_url,
    build_sdk_url,
    decode_work_secret,
)

logger = logging.getLogger(__name__)


# ── Public types ──────────────────────────────────────────────────────────


BridgeState = str
"""``'ready'`` | ``'connected'`` | ``'reconnecting'`` | ``'failed'``."""


# Forward references via Any so we don't have to pre-define types in this
# already-busy module. Real Message / SDK types live in their own modules.
OnInboundMessage = Callable[[dict[str, Any]], Any]
OnUserMessage = Callable[[str, str], bool]
OnPermissionResponse = Callable[[dict[str, Any]], None]
OnInterrupt = Callable[[], None]
OnSetModel = Callable[[str | None], None]
OnSetMaxThinkingTokens = Callable[[int | None], None]
OnSetPermissionMode = Callable[[str], Any]
OnStateChange = Callable[..., None]
OnAuth401 = Callable[[str], Awaitable[bool]]
GetAccessToken = Callable[[], str | None]


@dataclass
class BridgeCoreParams:
    """Explicit-param input to ``init_bridge_core``.

    Mirrors TS ``BridgeCoreParams`` on ``replBridge.ts:92-222``. Required
    fields first; everything optional defaults sensibly.
    """

    # Identity
    dir: str
    machine_name: str
    branch: str
    git_repo_url: str | None
    title: str

    # URLs
    base_url: str
    session_ingress_url: str
    worker_type: str

    # Auth
    get_access_token: GetAccessToken

    # Session creation (injected for daemon vs REPL flexibility)
    create_session: Callable[[dict[str, Any]], Awaitable[str | None]]
    """``async def create_session({environment_id, title, gitRepoUrl, branch})
    -> session_id | None``. Daemon/REPL wrappers pass distinct implementations
    that differ in how they build the org-scoped HTTP headers."""

    archive_session: Callable[[str], Awaitable[None]]
    """``async def archive_session(session_id)`` — best-effort archival
    on teardown; MUST NOT throw."""

    # Optional callbacks
    on_auth_401: OnAuth401 | None = None
    on_inbound_message: OnInboundMessage | None = None
    on_user_message: OnUserMessage | None = None
    on_permission_response: OnPermissionResponse | None = None
    on_interrupt: OnInterrupt | None = None
    on_set_model: OnSetModel | None = None
    on_set_max_thinking_tokens: OnSetMaxThinkingTokens | None = None
    on_set_permission_mode: OnSetPermissionMode | None = None
    on_state_change: OnStateChange | None = None

    # Config getters
    get_poll_interval_config: Callable[[], PollIntervalConfig] = (
        lambda: DEFAULT_POLL_CONFIG
    )
    get_current_title: Callable[[], str] | None = None

    # Identity for the env registration
    max_sessions: int = 1
    spawn_mode: str = 'single-session'  # 'single-session' | 'same-dir' | 'worktree'
    bridge_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # MVP scope: perpetual mode is not yet supported.
    perpetual: bool = False

    # Initial history (currently unused by the MVP slice — recorded for
    # future Phase 6 work that integrates with remote_bridge_core's
    # flush_gate pattern).
    initial_messages: list[Any] | None = None
    initial_history_cap: int = 200

    # Max attempts to recreate the environment after a poll 404 / expired
    # error. Mirrors TS bridgeMain's 3-attempt envelope on
    # ``replBridge.ts:614-852``. Each attempt: re-register the env, then
    # create a fresh session, then resume polling.
    max_env_recreation_attempts: int = 3


@dataclass
class ReplBridgeHandle:
    """Opaque handle returned by ``init_bridge_core``.

    Mirrors TS ``ReplBridgeHandle`` on ``replBridge.ts:71-82``. All
    write methods are sync fire-and-forget; ``teardown`` is async and
    idempotent.
    """

    bridge_session_id: str
    environment_id: str
    session_ingress_url: str
    write_messages: Callable[[list[Any]], None]
    write_sdk_messages: Callable[[list[dict[str, Any]]], None]
    send_control_request: Callable[[dict[str, Any]], None]
    send_control_response: Callable[[dict[str, Any]], None]
    send_cancel_request: Callable[[str], None]
    send_result: Callable[[], None]
    teardown: Callable[[], Awaitable[None]]


# ── init_bridge_core ──────────────────────────────────────────────────────


async def init_bridge_core(
    params: BridgeCoreParams,
    *,
    http_client: httpx.AsyncClient | None = None,
    api_client: BridgeApiClient | None = None,
    spawner: SessionSpawner | None = None,
    runner_version: str = 'py-bridge-mvp',
) -> ReplBridgeHandle | None:
    """Set up the env-based bridge: register → create session → start poll loop.

    Returns ``None`` on any pre-flight failure (no OAuth, env registration
    failed, initial session creation failed). The returned handle stays
    alive until ``teardown()`` is called or the (single) session ends.

    Test seams (kw-only):

    * ``http_client``: optional ``httpx.AsyncClient`` for the bridge API.
    * ``api_client``: pre-built ``BridgeApiClient`` (overrides
      ``http_client`` if provided). Tests use this to inject fakes.
    * ``spawner``: pre-built ``SessionSpawner``. Tests use this to skip
      the real subprocess.
    * ``runner_version``: header value for ``x-environment-runner-version``.
    """
    if params.perpetual:
        raise NotImplementedError(
            'Phase 6 MVP does not yet implement perpetual mode '
            '(crash-recovery pointer + env reuse). Full port lands in a '
            'future revision; set perpetual=False for now.'
        )

    if api_client is None:
        api_client = create_bridge_api_client(
            base_url=params.base_url,
            get_access_token=params.get_access_token,
            runner_version=runner_version,
            on_auth_401=params.on_auth_401,
            client=http_client,
        )

    # ── 1. Register environment ────────────────────────────────────────
    bridge_config = BridgeConfig(
        dir=params.dir,
        machine_name=params.machine_name,
        branch=params.branch,
        git_repo_url=params.git_repo_url,
        max_sessions=params.max_sessions,
        spawn_mode=_validated_spawn_mode(params.spawn_mode),
        verbose=False,
        sandbox=False,
        bridge_id=params.bridge_id,
        worker_type=params.worker_type,
        environment_id=params.bridge_id,  # client-generated; server may swap
        api_base_url=params.base_url,
        session_ingress_url=params.session_ingress_url,
    )
    try:
        registration = await api_client.register_bridge_environment(
            bridge_config
        )
    except BridgeFatalError as err:
        logger.error('[bridge:repl] Registration failed: %s', err)
        _fire_state(params.on_state_change, 'failed',
                    f'Registration failed: {err}')
        return None
    environment_id = registration['environment_id']
    environment_secret = registration['environment_secret']
    logger.debug(
        '[bridge:repl] Registered environment_id=%s', environment_id
    )

    # ── 2. Create initial session ──────────────────────────────────────
    try:
        session_id = await params.create_session({
            'environment_id': environment_id,
            'title': params.title,
            'gitRepoUrl': params.git_repo_url,
            'branch': params.branch,
        })
    except Exception as err:  # noqa: BLE001
        logger.error('[bridge:repl] Session creation threw: %s', err)
        session_id = None
    if session_id is None:
        _fire_state(params.on_state_change, 'failed',
                    'Session creation failed')
        try:
            await api_client.deregister_environment(environment_id)
        except Exception as err:  # noqa: BLE001
            logger.debug(
                '[bridge:repl] Deregister-after-create-fail failed: %s', err
            )
        return None
    logger.debug('[bridge:repl] Created session_id=%s', session_id)

    # ── 3. Build the spawner (if not test-injected) ────────────────────
    if spawner is None:
        spawner = create_session_spawner(SessionSpawnerDeps(
            exec_path='claude',  # caller-overridable in future
            verbose=False,
            sandbox=False,
        ))

    # ── 4. State machine + poll loop ──────────────────────────────────
    state = _BridgeState(
        params=params,
        api=api_client,
        spawner=spawner,
        environment_id=environment_id,
        environment_secret=environment_secret,
        initial_session_id=session_id,
        bridge_config=bridge_config,
    )
    state.start_poll_loop()

    _fire_state(params.on_state_change, 'ready')

    return ReplBridgeHandle(
        bridge_session_id=session_id,
        environment_id=environment_id,
        session_ingress_url=params.session_ingress_url,
        write_messages=state.write_messages,
        write_sdk_messages=state.write_sdk_messages,
        send_control_request=state.send_control_request,
        send_control_response=state.send_control_response,
        send_cancel_request=state.send_cancel_request,
        send_result=state.send_result,
        teardown=state.teardown,
    )


# ── Internal state machine ────────────────────────────────────────────────


@dataclass
class _BridgeState:
    """Shared mutable state for one bridge."""

    params: BridgeCoreParams
    api: BridgeApiClient
    spawner: SessionSpawner
    environment_id: str
    environment_secret: str
    initial_session_id: str
    bridge_config: BridgeConfig

    poll_task: asyncio.Task[None] | None = None
    poll_cancel: asyncio.Event = field(default_factory=asyncio.Event)
    active_session: SessionHandle | None = None
    active_work_id: str | None = None
    active_session_id: str | None = None
    active_token_refresh: TokenRefreshScheduler | None = None
    torn_down: bool = False

    # Per-session telemetry-style counters. Dropped batches is a count
    # of times a write to the child's stdin failed (broken pipe, etc.)
    # — surfaces silent message loss that would otherwise be invisible.
    dropped_batch_count: int = 0
    env_recreation_attempts: int = 0

    # ── Poll loop ───────────────────────────────────────────────────────

    def start_poll_loop(self) -> None:
        self.poll_task = asyncio.create_task(
            self._poll_loop(),
            name=f'bridge-poll-{self.environment_id}',
        )

    async def _poll_loop(self) -> None:
        cfg = self.params.get_poll_interval_config()
        interval = cfg.poll_interval_ms_not_at_capacity / 1000.0
        while not self.poll_cancel.is_set() and not self.torn_down:
            try:
                if self.active_session is not None:
                    # At capacity for the MVP — single session at a time.
                    # Sleep at the at-capacity interval, then re-check.
                    await self._sleep_or_cancel(
                        cfg.poll_interval_ms_at_capacity / 1000.0
                    )
                    continue

                work = await self.api.poll_for_work(
                    self.environment_id, self.environment_secret,
                )
                if work is None:
                    await self._sleep_or_cancel(interval)
                    continue

                await self._process_work(work)
            except BridgeFatalError as err:
                if is_expired_error_type(err.error_type) or err.status == 404:
                    # Env-recreation flow (Phase 11b, mirrors TS Strategy-2
                    # on ``replBridge.ts:822``). Re-register the env from
                    # scratch + create a fresh session, then keep polling.
                    # Bounded by ``max_env_recreation_attempts`` to avoid
                    # infinite loops on a permanently-broken backend.
                    if self.env_recreation_attempts >= (
                        self.params.max_env_recreation_attempts
                    ):
                        logger.error(
                            '[bridge:repl] Env recreation exhausted '
                            '(%s attempts); giving up: %s',
                            self.env_recreation_attempts, err,
                        )
                        _fire_state(
                            self.params.on_state_change, 'failed',
                            f'Env recreation exhausted ({err.status})',
                        )
                        return
                    self.env_recreation_attempts += 1
                    logger.warning(
                        '[bridge:repl] Environment lost (%s); '
                        'recreating (attempt %s/%s)',
                        err.status,
                        self.env_recreation_attempts,
                        self.params.max_env_recreation_attempts,
                    )
                    _fire_state(
                        self.params.on_state_change, 'reconnecting',
                        f'Env recreation attempt '
                        f'{self.env_recreation_attempts}',
                    )
                    if await self._recreate_environment():
                        # Reset the attempt counter on success — a future
                        # 404 starts fresh rather than counting against
                        # this one's budget.
                        self.env_recreation_attempts = 0
                        _fire_state(
                            self.params.on_state_change, 'ready',
                        )
                        continue
                    # Recreation itself failed; loop will retry on next
                    # iteration (the attempt counter persists).
                    await self._sleep_or_cancel(interval)
                    continue
                logger.error('[bridge:repl] Poll fatal error: %s', err)
                _fire_state(
                    self.params.on_state_change, 'failed', str(err),
                )
                return
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as err:  # noqa: BLE001
                logger.warning('[bridge:repl] Poll loop error: %s', err)
                await self._sleep_or_cancel(interval)

    async def _sleep_or_cancel(self, seconds: float) -> None:
        """Sleep up to ``seconds``, waking early on cancellation."""
        try:
            await asyncio.wait_for(self.poll_cancel.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _recreate_environment(self) -> bool:
        """Re-register the environment + create a fresh session.

        Mirrors TS Strategy-2 on ``replBridge.ts:822``. Returns True on
        success (caller resets the attempt counter), False on failure
        (caller backs off and retries; the attempt counter persists so
        we eventually give up).

        Strategy-1 (in-place ``reconnect_session`` to keep the same
        session ID + SSE seq-num) is deferred — it needs the
        ``BridgeApiClient.reconnect_session`` happy path which the MVP
        doesn't exercise, and the session-runner doesn't yet support
        live resumption with a different ingress URL.
        """
        # If there's an active session, archive it before starting fresh.
        if self.active_session is not None:
            try:
                self.active_session.kill()
            except Exception as err:  # noqa: BLE001
                logger.debug(
                    '[bridge:repl] kill during recreation: %s', err
                )
            self.active_session = None
            self.active_work_id = None
            self.active_session_id = None
            if self.active_token_refresh is not None:
                self.active_token_refresh.cancel_all()
                self.active_token_refresh = None
        # Best-effort archive of the prior session id.
        try:
            await self.params.archive_session(self.initial_session_id)
        except Exception as err:  # noqa: BLE001
            logger.debug(
                '[bridge:repl] archive of prior session failed '
                'during recreation: %s', err
            )
        # Re-register environment (server may hand back a fresh
        # environment_id; we accept whatever it gives us).
        try:
            registration = await self.api.register_bridge_environment(
                self.bridge_config,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:repl] Re-register failed: %s', err
            )
            return False
        self.environment_id = registration['environment_id']
        self.environment_secret = registration['environment_secret']
        # Create a fresh session on the new environment.
        try:
            new_session_id = await self.params.create_session({
                'environment_id': self.environment_id,
                'title': self.params.title,
                'gitRepoUrl': self.params.git_repo_url,
                'branch': self.params.branch,
            })
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:repl] create_session during recreation '
                'failed: %s', err,
            )
            return False
        if new_session_id is None:
            logger.warning(
                '[bridge:repl] create_session during recreation returned None'
            )
            return False
        # Replace the bookkeeping handle's session id (the external
        # ReplBridgeHandle is immutable; this is the internal copy).
        self.initial_session_id = new_session_id
        logger.info(
            '[bridge:repl] Environment recreated: env=%s session=%s',
            self.environment_id, new_session_id,
        )
        return True

    async def _process_work(self, work: dict[str, Any]) -> None:
        """Handle one work item from the poll."""
        work_id = work.get('id')
        if not isinstance(work_id, str):
            logger.warning('[bridge:repl] Work missing id: %s', work)
            return
        data = work.get('data') or {}
        if not isinstance(data, dict):
            logger.warning('[bridge:repl] Work missing data: %s', work)
            return
        work_type = data.get('type')
        if work_type == 'healthcheck':
            # Acknowledge and move on.
            await self._safe_ack(work_id, self.environment_secret)
            return
        if work_type != 'session':
            logger.warning(
                '[bridge:repl] Unknown work type: %s', work_type
            )
            return

        # Decode the work secret to get the session token + URL.
        try:
            secret = decode_work_secret(work.get('secret') or '')
        except Exception as err:  # noqa: BLE001
            logger.error(
                '[bridge:repl] Failed to decode work secret: %s', err
            )
            await self._safe_stop_work(work_id, force=True)
            return

        session_id = data.get('id')
        if not isinstance(session_id, str):
            logger.warning('[bridge:repl] Work session.id missing')
            return

        # Acknowledge — claims the work item so the server doesn't
        # redispatch it after the reclaim window.
        await self._safe_ack(work_id, secret.session_ingress_token)

        # MVP: v2 transport only (CCR). Detect v1 (session-ingress WS)
        # via the URL and refuse for now.
        use_ccr_v2 = bool(secret.use_code_sessions)
        if not use_ccr_v2:
            logger.warning(
                '[bridge:repl] v1 (session-ingress) transport not yet '
                'implemented in Phase 6 MVP — v2 only. Stopping work.'
            )
            await self._safe_stop_work(work_id, force=True)
            return
        sdk_url = build_ccr_v2_sdk_url(secret.api_base_url, session_id)
        # NOTE: Phase 6 full port will fetch worker_epoch via the v2
        # /worker/register call. The MVP uses 0 as a placeholder since
        # session_runner threads it into the child's env vars only when
        # use_ccr_v2 is True.

        # Spawn the child.
        spawn_opts: SessionSpawnOpts = {
            'session_id': session_id,
            'sdk_url': sdk_url,
            'access_token': secret.session_ingress_token,
            'use_ccr_v2': use_ccr_v2,
            'worker_epoch': 0,
        }
        try:
            self.active_session = self.spawner.spawn(
                spawn_opts, self.params.dir,
            )
        except Exception as err:  # noqa: BLE001
            logger.error('[bridge:repl] Spawn failed: %s', err)
            await self._safe_stop_work(work_id, force=True)
            return
        self.active_work_id = work_id
        self.active_session_id = session_id
        # Wire JWT refresh: token expires after a finite window
        # (typically 1h); without a refresh, long sessions silently
        # break when the ingress token expires. The scheduler fires
        # before expiry and pushes a fresh token to the child via
        # session.update_access_token (which sends an
        # update_environment_variables NDJSON line on stdin).
        self.active_token_refresh = self._build_token_refresh_scheduler()
        # Use the work-secret JWT's expires_in if present; else fall
        # back to scheduler defaults. The work-secret payload doesn't
        # currently surface expires_in (TS reads it from WorkSecret too;
        # MVP doesn't decode that field), so we use the JWT's own
        # ``exp`` claim via ``schedule`` rather than ``schedule_from_expires_in``.
        try:
            self.active_token_refresh.schedule(
                session_id, secret.session_ingress_token,
            )
        except Exception as err:  # noqa: BLE001
            logger.debug(
                '[bridge:repl] schedule refresh failed (likely '
                'undecodable JWT — child uses initial token): %s', err
            )
        _fire_state(self.params.on_state_change, 'connected')
        logger.debug(
            '[bridge:repl] Spawned session_id=%s work_id=%s',
            session_id, work_id,
        )

        # Wait for the session to complete, then clean up.
        asyncio.create_task(
            self._await_session_done(work_id),
            name=f'bridge-session-await-{session_id}',
        )

    def _build_token_refresh_scheduler(self) -> TokenRefreshScheduler:
        """Create a scheduler whose ``on_refresh`` forwards to the child."""
        def on_refresh(_session_id: str, fresh_token: str) -> None:
            session = self.active_session
            if session is None:
                return
            try:
                session.update_access_token(fresh_token)
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    '[bridge:repl] update_access_token via stdin '
                    'failed: %s', err
                )

        async def get_access_token() -> str | None:
            # OAuth token getter for the refresh chain. Bridge sessions
            # never need to refresh the OAuth token themselves (the
            # ingress JWT is independently re-issued by the server's
            # poll flow) — return None so the scheduler treats this as
            # "no proactive OAuth refresh needed" and just fires the
            # follow-up timer.
            return self.params.get_access_token()

        return TokenRefreshScheduler(
            get_access_token=get_access_token,
            on_refresh=on_refresh,
            label='repl-bridge',
        )

    async def _await_session_done(self, work_id: str) -> None:
        if self.active_session is None:
            return
        try:
            status = await self.active_session.wait_done()
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:repl] wait_done raised: %s', err
            )
            status = 'failed'
        logger.debug(
            '[bridge:repl] Session done (status=%s)', status
        )
        # Cancel the JWT refresh scheduler — the session is done so any
        # pending refresh would write to a dead stdin.
        if self.active_token_refresh is not None:
            self.active_token_refresh.cancel_all()
            self.active_token_refresh = None
        # Stop the work item to free the server-side lease.
        await self._safe_stop_work(work_id, force=False)
        self.active_session = None
        self.active_work_id = None
        self.active_session_id = None

    async def _safe_ack(self, work_id: str, session_token: str) -> None:
        try:
            await self.api.acknowledge_work(
                self.environment_id, work_id, session_token,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:repl] ack failed for work_id=%s: %s',
                work_id, err,
            )

    async def _safe_stop_work(self, work_id: str, *, force: bool) -> None:
        try:
            await self.api.stop_work(
                self.environment_id, work_id, force,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:repl] stop_work failed for work_id=%s: %s',
                work_id, err,
            )

    # ── Public handle methods (MVP) ────────────────────────────────────
    # The MVP wires the write methods to the active session's stdin via
    # NDJSON. Phase 6 full port will use the bridge's events endpoint
    # (POST /v1/sessions/{id}/events) for some of these — particularly
    # control_response — but the simpler "forward to child stdin"
    # pattern matches what the env-based path historically did.

    def write_messages(self, messages: list[Any]) -> None:
        """Forward local messages to the child via stdin (MVP).

        Phase 6 full port: route through the bridge events POST so
        messages also reach claude.ai. The MVP just forwards to the
        child so the local session sees them. Failures bump
        ``dropped_batch_count`` so silent message loss is observable.
        """
        if self.active_session is None or not messages:
            return
        # MVP: serialize each message as an NDJSON line and write to
        # the child stdin. The real wire format is more elaborate (see
        # message_mappers.to_sdk_messages) and is wired in Phase 6 full.
        import json
        for msg in messages:
            try:
                line = json.dumps({
                    'type': 'user',
                    'message': {
                        'role': 'user',
                        'content': getattr(msg, 'content', ''),
                    },
                    'uuid': getattr(msg, 'uuid', None),
                }) + '\n'
                self.active_session.write_stdin(line)
            except Exception as err:  # noqa: BLE001
                self.dropped_batch_count += 1
                logger.warning(
                    '[bridge:repl] write_messages failed '
                    '(dropped_batch_count=%s): %s',
                    self.dropped_batch_count, err,
                )

    def write_sdk_messages(self, messages: list[dict[str, Any]]) -> None:
        """Forward pre-shaped SDK messages to the child (MVP)."""
        if self.active_session is None:
            return
        import json
        for msg in messages:
            try:
                self.active_session.write_stdin(json.dumps(msg) + '\n')
            except Exception as err:  # noqa: BLE001
                self.dropped_batch_count += 1
                logger.warning(
                    '[bridge:repl] write_sdk_messages failed '
                    '(dropped_batch_count=%s): %s',
                    self.dropped_batch_count, err,
                )

    def send_control_request(self, request: dict[str, Any]) -> None:
        if self.active_session is None:
            return
        import json
        self.active_session.write_stdin(json.dumps(request) + '\n')

    def send_control_response(self, response: dict[str, Any]) -> None:
        # Phase 6 full port: route via api.send_permission_response_event
        # (POST /v1/sessions/{id}/events) instead of the child's stdin.
        # The MVP keeps it on stdin for symmetry with write_messages.
        if self.active_session is None:
            return
        import json
        self.active_session.write_stdin(json.dumps(response) + '\n')

    def send_cancel_request(self, request_id: str) -> None:
        if self.active_session is None:
            return
        import json
        self.active_session.write_stdin(json.dumps({
            'type': 'control_cancel_request',
            'request_id': request_id,
        }) + '\n')

    def send_result(self) -> None:
        # MVP: no-op. The child emits its own result message when it
        # finishes a turn; this exists for API parity with remote_bridge_core.
        pass

    # ── Teardown ───────────────────────────────────────────────────────

    async def teardown(self) -> None:
        """Stop poll loop → kill active session → stop work → archive → deregister.

        Idempotent — safe to call multiple times.
        """
        if self.torn_down:
            return
        self.torn_down = True
        self.poll_cancel.set()
        if self.poll_task is not None:
            self.poll_task.cancel()
            try:
                await self.poll_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        # Cancel the JWT refresh scheduler so any pending refresh
        # doesn't write to a dead stdin during teardown.
        if self.active_token_refresh is not None:
            self.active_token_refresh.cancel_all()
            self.active_token_refresh = None

        # Kill the active session if any.
        if self.active_session is not None:
            try:
                self.active_session.kill()
            except Exception as err:  # noqa: BLE001
                logger.warning('[bridge:repl] kill failed: %s', err)
            # Give it a brief grace, then force.
            try:
                await asyncio.wait_for(
                    self.active_session.wait_done(), timeout=2.0,
                )
            except asyncio.TimeoutError:
                try:
                    self.active_session.force_kill()
                except Exception as err:  # noqa: BLE001
                    logger.warning(
                        '[bridge:repl] force_kill failed: %s', err
                    )

        # Stop any active work.
        if self.active_work_id is not None:
            await self._safe_stop_work(self.active_work_id, force=True)

        # Archive the initial session (best-effort).
        try:
            await self.params.archive_session(self.initial_session_id)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:repl] archive_session failed: %s', err
            )

        # Deregister the environment.
        try:
            await self.api.deregister_environment(self.environment_id)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:repl] deregister failed: %s', err
            )


# ── Helpers ───────────────────────────────────────────────────────────────


def _validated_spawn_mode(mode: str) -> Any:
    """Cast a user-supplied spawn-mode string to the Literal type."""
    if mode not in ('single-session', 'worktree', 'same-dir'):
        raise ValueError(f'Invalid spawn_mode: {mode!r}')
    return mode


def _fire_state(
    cb: OnStateChange | None,
    state: BridgeState,
    detail: str | None = None,
) -> None:
    if cb is None:
        return
    try:
        if detail is None:
            cb(state)
        else:
            cb(state, detail)
    except Exception as err:  # noqa: BLE001
        logger.warning(
            '[bridge:repl] on_state_change raised: %s', err
        )


__all__ = [
    'BridgeCoreParams',
    'BridgeState',
    'ReplBridgeHandle',
    'init_bridge_core',
]

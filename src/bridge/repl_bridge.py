"""Env-based bridge orchestrator — Phase 6 MVP slice.

Ports the **public surface + happy path** of
``typescript/src/bridge/replBridge.ts`` (~2400 lines in TS).

This module is the single-session bridge orchestrator. The
multi-session daemon variant lives in ``bridge_main.py``.

What IS ported in full:

* Public types: ``ReplBridgeHandle``, ``BridgeState``, ``BridgeCoreParams``
* ``init_bridge_core(params, *, http_client?, api_client?, spawner?)`` — the factory
* Single-session lifecycle: register → poll → spawn → done → archive
* Idempotent teardown
* OAuth + env-secret auth via ``bridge_api``
* Dual v1 (session-ingress WS) / v2 (CCR) dispatch — phase 14c
* Crash-recovery pointer + perpetual mode — phase 12c
* Env recreation: Strategy-1 in-place reconnect via
  ``api.reconnect_session``; Strategy-2 kill+create-fresh on
  poll 404 — phase 12b
* Init-time pointer session validation via ``reconnect_session`` —
  phase 13
* JWT refresh with v1/v2-aware dispatch (v1: push token to child
  stdin; v2: trigger server re-dispatch via ``reconnect_session``)
  — phase 15

What is **explicitly deferred**:

* **Multi-session** — this module handles one session at a time;
  second poll result is rejected. ``bridge_main.py`` is the
  multi-session daemon orchestrator.
* **v2 JWT refresh round-trip is best-effort in this module.**
  Phase 15 wires the v2 ``on_refresh`` callback to call
  ``api.reconnect_session(env_id, session_id)``, which causes the
  server to re-dispatch the work item with a fresh JWT. **But**
  the poll loop here (``_poll_loop``) skips polling while
  ``active_session is not None``, so the re-dispatch sits in the
  server queue and may expire its lease before the active session
  completes. The existingHandle path in ``_process_work`` will
  route the fresh JWT correctly **if and only if** the poll
  happens to fire between the reconnect and lease expiry —
  unlikely in practice. v2 long-running sessions on this
  single-session bridge will silently die at JWT expiry; for
  guaranteed v2 refresh delivery, use ``bridge_main.py``'s
  multi-session daemon which polls continuously at the
  at-capacity interval.
* **Backoff/give-up logic** — the poll loop uses a fixed interval
  from the config. The full TS backoff machinery (two-track error
  counters, process-suspension detection, 10-min give-up) lands
  in a future phase.
* **Dropped-batch telemetry** + **work-id completion dedup** — both
  log-only enhancements; deferred.
"""

from __future__ import annotations

import asyncio
import logging
import time
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
from src.bridge.bridge_pointer import (
    BridgePointer,
    clear_pointer,
    read_pointer,
    write_pointer,
)
from src.bridge.jwt_utils import TokenRefreshScheduler
from src.bridge.poll_config_defaults import (
    DEFAULT_POLL_CONFIG,
    PollIntervalConfig,
)
from src.bridge.session_id_compat import (
    to_compat_session_id,
    to_infra_session_id,
)
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


# Phase 17: hourly pointer-mtime refresh interval for perpetual mode.
# Mirrors TS ``replBridge.ts:1541`` (``60 * 60_000`` ms = 1 hour).
# Long-running sessions touch the pointer once per interval so the
# mtime stays fresh; without this, a future TTL check on the pointer
# would reject it after a few hours of no user activity.
POINTER_MTIME_REFRESH_INTERVAL_S = 60.0 * 60.0


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
    if api_client is None:
        api_client = create_bridge_api_client(
            base_url=params.base_url,
            get_access_token=params.get_access_token,
            runner_version=runner_version,
            on_auth_401=params.on_auth_401,
            client=http_client,
        )

    # ── 0. Perpetual mode: try crash-recovery pointer ──────────────────
    # Mirrors TS ``initReplBridge`` / ``replBridge.ts`` recovery dance:
    # if the previous run left a pointer, attempt to reuse its env id
    # (server may resurrect the lease) and its session id (subprocess
    # restart resumes the same conversation). Any failure — pointer
    # absent, stale, register-with-reuse rejected, etc. — drops us
    # back into the fresh-env+fresh-session bootstrap path.
    pointer: BridgePointer | None = None
    reuse_session_id: str | None = None
    pointer_created_at_ms: int | None = None
    effective_bridge_id = params.bridge_id
    if params.perpetual:
        pointer = read_pointer(
            params.dir, machine_name=params.machine_name,
        )
        if pointer is not None:
            logger.info(
                '[bridge:repl] Perpetual: found recovery pointer '
                'bridge_id=%s env=%s session=%s — attempting reuse',
                pointer.bridge_id, pointer.environment_id,
                pointer.session_id,
            )
            effective_bridge_id = pointer.bridge_id
            reuse_session_id = pointer.session_id
            pointer_created_at_ms = pointer.created_at_ms

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
        bridge_id=effective_bridge_id,
        worker_type=params.worker_type,
        # client-generated placeholder; the server returns the real
        # env id in the registration response and we overwrite then.
        environment_id=effective_bridge_id,
        api_base_url=params.base_url,
        session_ingress_url=params.session_ingress_url,
        # Hint server to resurrect the pointer's env. If the server
        # ignores it (env lease expired), it'll just assign a fresh
        # id and we'll fall through to creating a new session.
        reuse_environment_id=(
            pointer.environment_id if pointer is not None else None
        ),
    )
    try:
        registration = await api_client.register_bridge_environment(
            bridge_config
        )
    except BridgeFatalError as err:
        logger.error('[bridge:repl] Registration failed: %s', err)
        _fire_state(params.on_state_change, 'failed',
                    f'Registration failed: {err}')
        # A stale pointer that the server fully rejects (rather than
        # just declining to reuse) is dead weight — clear it so the
        # next start doesn't re-fail the same way.
        if params.perpetual:
            clear_pointer(params.dir)
        return None
    environment_id = registration['environment_id']
    environment_secret = registration['environment_secret']
    logger.debug(
        '[bridge:repl] Registered environment_id=%s', environment_id
    )

    # If we asked for env reuse and the server gave us back a DIFFERENT
    # env id, the resurrection didn't happen. The reuse_session_id
    # we'd captured from the pointer is bound to the dead env and
    # would be useless against the new env, so drop it — create_session
    # will mint a fresh one below.
    #
    # Phase 16 / phase-13 follow-up: also clear the stale pointer
    # eagerly. Phase 12c's original behavior relied on the post-
    # ``create_session`` pointer write to overwrite the stale entry,
    # but if ``create_session`` itself fails after this branch fires,
    # the stale pointer survives on disk and a subsequent restart
    # would re-hint the dead env again. TS clears on any failure to
    # reuse the prior pointer (``prior && !reusedPriorSession`` at
    # ``replBridge.ts:429-431``) — Python mirrors this with two
    # clears: here for the env-mismatch case, and at the reconnect-
    # validation block below for the all-candidates-refused case.
    if (
        pointer is not None
        and registration['environment_id'] != pointer.environment_id
    ):
        logger.info(
            '[bridge:repl] Perpetual: server didn\'t resurrect env '
            '(pointer=%s, got=%s) — falling back to fresh session',
            pointer.environment_id, registration['environment_id'],
        )
        reuse_session_id = None
        clear_pointer(params.dir)

    # ── 2. Validate the pointer's session id, reuse or create ──────────
    # Phase 13: before trusting ``reuse_session_id``, actively probe the
    # server via ``reconnect_session``. Without this, a session archived
    # between restarts would resurface only after a full 404 poll cycle.
    # Try both ``session_*`` and ``cse_*`` tags because the pointer was
    # written under an unknown v2-compat-gate state (see TS
    # ``replBridge.ts:392-415`` for the same rationale).
    if reuse_session_id is not None:
        candidates: list[str] = [reuse_session_id]
        infra_id = to_infra_session_id(reuse_session_id)
        if infra_id != reuse_session_id:
            candidates.append(infra_id)
        reconnect_ok = False
        for candidate in candidates:
            try:
                await api_client.reconnect_session(
                    environment_id, candidate,
                )
            # Intentionally broad (mirrors TS replBridge.ts:410):
            # transient failures (5xx, network) conservatively fall
            # through to fresh session rather than risk reusing a
            # session whose state is undefined. ``CancelledError``
            # inherits from ``BaseException`` and is not caught here.
            except Exception as err:  # noqa: BLE001
                logger.debug(
                    '[bridge:repl] reconnect_session(%s) failed: %s',
                    candidate, err,
                )
                continue
            logger.debug(
                '[bridge:repl] reconnect_session(%s) succeeded',
                candidate,
            )
            reconnect_ok = True
            break
        if not reconnect_ok:
            logger.info(
                '[bridge:repl] Perpetual: session %s no longer reachable '
                '(all %d candidate(s) refused) — creating fresh',
                reuse_session_id, len(candidates),
            )
            clear_pointer(params.dir)
            reuse_session_id = None

    session_id: str | None
    if reuse_session_id is not None:
        session_id = reuse_session_id
        logger.info(
            '[bridge:repl] Perpetual: reusing session_id=%s '
            '(reconnect-validated)', session_id,
        )
    else:
        try:
            session_id = await params.create_session({
                'environment_id': environment_id,
                'title': params.title,
                'gitRepoUrl': params.git_repo_url,
                'branch': params.branch,
            })
        except Exception as err:  # noqa: BLE001
            logger.error(
                '[bridge:repl] Session creation threw: %s', err
            )
            session_id = None
        if session_id is None:
            _fire_state(params.on_state_change, 'failed',
                        'Session creation failed')
            try:
                await api_client.deregister_environment(environment_id)
            except Exception as err:  # noqa: BLE001
                logger.debug(
                    '[bridge:repl] Deregister-after-create-fail failed: %s',
                    err,
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
        perpetual=params.perpetual,
        pointer_created_at_ms=pointer_created_at_ms,
    )
    # Write the pointer immediately so a crash before the first poll
    # still leaves a recoverable state for the next start. Compute the
    # ``created_at_ms`` locally — passing it both to ``write_pointer``
    # AND storing it on ``state.pointer_created_at_ms`` ensures future
    # updates preserve the original timestamp. Doing this via a
    # read-back from the file would silently lose the value if the
    # write failed (write_pointer is best-effort and logs-and-swallows).
    if params.perpetual:
        if pointer_created_at_ms is None:
            pointer_created_at_ms = int(time.time() * 1000)
        state.pointer_created_at_ms = pointer_created_at_ms
        write_pointer(
            params.dir,
            bridge_id=effective_bridge_id,
            environment_id=environment_id,
            session_id=session_id,
            machine_name=params.machine_name,
            created_at_ms=pointer_created_at_ms,
        )
        # Phase 17: start the periodic mtime-refresh task. Skipped in
        # non-perpetual mode since the pointer is cleared on teardown
        # anyway.
        state.pointer_mtime_task = asyncio.create_task(
            state._pointer_mtime_refresh_loop(),
            name='bridge-repl-pointer-mtime-refresh',
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
    #: Phase 15 — v1/v2 flag of the currently-active work item, used
    #: by the JWT refresh callback to route between
    #: ``session.update_access_token`` (v1) and
    #: ``api.reconnect_session`` (v2). Set in ``_process_work``;
    #: cleared in ``_await_session_done``.
    active_use_ccr_v2: bool = False
    torn_down: bool = False

    # Per-session telemetry-style counters. Dropped batches is a count
    # of times a write to the child's stdin failed (broken pipe, etc.)
    # — surfaces silent message loss that would otherwise be invisible.
    dropped_batch_count: int = 0
    env_recreation_attempts: int = 0

    # Phase 12c: perpetual mode + crash-recovery pointer state.
    # ``perpetual`` decides whether to write/update/clear the pointer
    # on lifecycle events. ``pointer_created_at_ms`` carries forward
    # the pointer's original creation timestamp across recreations so
    # operators can see how long a perpetual bridge has been alive.
    perpetual: bool = False
    pointer_created_at_ms: int | None = None
    #: Phase 17: periodic pointer-mtime refresh task. Started in
    #: perpetual mode at init; touches the pointer once per hour so
    #: long-running sessions don't leave a stale mtime that triggers
    #: the next-start TTL check (when one lands). Cancelled in teardown.
    pointer_mtime_task: asyncio.Task[None] | None = None

    async def _update_pointer(self, *, session_id: str | None) -> None:
        """No-op when not perpetual; otherwise rewrite the pointer with
        the current env_id + given session_id. Called at every
        lifecycle transition (init, work-spawned, session-done,
        recreate) so a crash always leaves a recoverable on-disk state.

        ``write_pointer`` does synchronous file IO; we delegate it to
        a worker thread so a slow disk (NFS, etc.) can't stall the
        event loop. Best-effort — failures are logged by the writer.
        """
        if not self.perpetual:
            return
        await asyncio.to_thread(
            write_pointer,
            self.params.dir,
            bridge_id=self.bridge_config.bridge_id,
            environment_id=self.environment_id,
            session_id=session_id,
            machine_name=self.params.machine_name,
            created_at_ms=self.pointer_created_at_ms,
        )

    async def _clear_pointer(self) -> None:
        """No-op when not perpetual; otherwise remove the pointer
        file. Called when the bridge tears down cleanly so the next
        start doesn't try to resume a state that's no longer valid.

        Also off-loaded to a worker thread (see ``_update_pointer``)."""
        if not self.perpetual:
            return
        await asyncio.to_thread(clear_pointer, self.params.dir)

    async def _pointer_mtime_refresh_loop(self) -> None:
        """Phase 17: periodic pointer-mtime refresh for perpetual mode.

        Mirrors TS ``replBridge.ts:1522-1543`` — a daemon idle for
        many hours without a user prompt would have a stale pointer
        mtime; when the next-start TTL check (future phase) lands, it
        would reject the pointer and force a fresh session. Touching
        the pointer hourly keeps long-running sessions recoverable.

        The write is atomic via ``write_pointer``'s tmpfile + os.replace
        primitive, so a race with ``_recreate_environment``'s own
        pointer write cannot leave a half-corrupt file. The race is
        benign in the common case (Strategy-1 updates env+session in
        a tight window) but **narrowly inconsistent in Strategy-2**:
        between the env-id swap and the new session-id assignment,
        ``active_session_id`` is briefly ``None`` while
        ``environment_id`` is already the new one. A refresh tick
        landing in that window writes ``(new_env_id, session_id=None)``;
        if the refresh's write *follows* Strategy-2's final write,
        the pointer ends as ``(new_env_id, None)`` instead of
        ``(new_env_id, new_session_id)``. The next start would resume
        the env but mint a fresh session — recoverable, not corrupt.
        Strategy-2 has already killed the prior session by that point,
        so user-visible continuity was already lost. TS guards against
        this with a ``reconnectPromise`` skip; we accept the benign
        race in exchange for a simpler implementation.

        Loop exits on ``CancelledError`` (teardown cancels the task,
        catches cancellation from either the sleep or the inner
        ``_update_pointer`` await).
        """
        try:
            while not self.torn_down:
                await asyncio.sleep(POINTER_MTIME_REFRESH_INTERVAL_S)
                if self.torn_down:
                    return
                # ``active_session_id`` may be None between sessions
                # (post ``_await_session_done``); ``_update_pointer``
                # handles None correctly by writing a null session_id.
                await self._update_pointer(
                    session_id=self.active_session_id,
                )
        except asyncio.CancelledError:
            return

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
        """Re-register the environment, then **try Strategy-1 reconnect
        first** (preserve the active session via ``reconnect_session``
        when the server resurrects the *same* env id); fall back to
        **Strategy-2** (kill + create fresh session) otherwise.

        Mirrors TS ``replBridge.ts:614-852``. Returns True on success
        (caller resets the attempt counter), False on failure (caller
        backs off and retries; the attempt counter persists so we
        eventually give up).

        Strategy-1 preserves the local session subprocess and its
        in-flight CCR client connection. It does NOT independently
        preserve the SSE seq-num — that depends on the CCR client
        surviving the env change, which today is best-effort.
        """
        # Strategy-1 only makes sense if the server hands back the
        # SAME env id (TS ``tryReconnectInPlace`` at replBridge.ts:386-391
        # bails when ``environmentId !== requestedEnvId``). Hint the
        # server to reuse by setting ``reuse_environment_id`` before
        # registering; restore it after so a future Strategy-2 cycle
        # gets a fresh env if the server doesn't want to reuse.
        prior_env_id = self.environment_id
        prior_reuse = self.bridge_config.reuse_environment_id
        # ``active_session_id`` reflects the currently-running work
        # item (may differ from ``initial_session_id`` after earlier
        # recreations). Strategy-1 preserves the *active* session.
        prior_session_id = self.active_session_id
        prior_work_id = self.active_work_id
        had_active_session = self.active_session is not None
        self.bridge_config.reuse_environment_id = prior_env_id
        try:
            try:
                registration = await self.api.register_bridge_environment(
                    self.bridge_config,
                )
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    '[bridge:repl] Re-register failed: %s', err
                )
                return False
        finally:
            self.bridge_config.reuse_environment_id = prior_reuse
        new_env_id = registration['environment_id']
        new_env_secret = registration['environment_secret']

        # ── Strategy-1: in-place reconnect ──────────────────────────
        # Gate on (a) same env id AND (b) active session id AND (c)
        # active session handle. (a) is the TS preconditon; (b) and (c)
        # together mean there's a real session to preserve. If the
        # server changed env id (despite our reuse hint), Strategy-1
        # is unreachable — the prior session was bound to the dead env.
        if (
            new_env_id == prior_env_id
            and prior_session_id is not None
            and had_active_session
        ):
            try:
                await self.api.reconnect_session(
                    new_env_id, prior_session_id,
                )
            except Exception as err:  # noqa: BLE001
                logger.info(
                    '[bridge:repl] Strategy-1 reconnect refused '
                    '(session=%s): %s — falling back to Strategy-2',
                    prior_session_id, err,
                )
            else:
                # Server accepted the reconnect. The OLD work item is
                # now stale (its work-secret was bound to the dead env
                # state) — clear it server-side and locally so the next
                # poll can pick up a fresh work-secret. The subprocess
                # itself keeps running; the next poll will redeliver
                # work for the same session_id with a fresh JWT.
                #
                # Invariant: ``new_env_id == prior_env_id`` here (the
                # gate above enforces it), so ``_safe_stop_work`` —
                # which reads ``self.environment_id`` — hits the right
                # env whether called before or after the swap below.
                if prior_work_id is not None:
                    await self._safe_stop_work(prior_work_id, force=False)
                    self.active_work_id = None
                self.environment_id = new_env_id
                self.environment_secret = new_env_secret
                # Phase 12c: env+session preserved; pointer just needs
                # a touch so ``updated_at_ms`` reflects the activity.
                await self._update_pointer(session_id=prior_session_id)
                logger.info(
                    '[bridge:repl] Strategy-1 reconnect succeeded: '
                    'env=%s session=%s (preserved)',
                    new_env_id, prior_session_id,
                )
                return True

        # ── Strategy-2: kill active session + create fresh ─────────
        # Capture the session id we should archive BEFORE nulling the
        # active fields below. Prefer the currently-running session id
        # (what the user was actually doing) over the bridge's initial
        # session id (which may be stale after an earlier recreation).
        archive_id = self.active_session_id or self.initial_session_id
        # If there's an active session, kill it before starting fresh.
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
            await self.params.archive_session(archive_id)
        except Exception as err:  # noqa: BLE001
            logger.debug(
                '[bridge:repl] archive of prior session failed '
                'during recreation: %s', err
            )
        # Adopt the new env handles before create_session (the server
        # binds the new session to whatever env we name).
        self.environment_id = new_env_id
        self.environment_secret = new_env_secret
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
        # Phase 12c: env+session were both swapped; rewrite the pointer
        # so a crash at this point recovers into the NEW state rather
        # than the dead one.
        await self._update_pointer(session_id=new_session_id)
        logger.info(
            '[bridge:repl] Strategy-2 recreate complete: env=%s session=%s',
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

        # Phase 15: existingHandle path. If this work item is for a
        # session that's already running (e.g. server re-dispatched
        # after a v2 JWT refresh's ``reconnect_session`` call), push
        # the fresh JWT to the live child and reschedule the refresh
        # — DO NOT spawn a duplicate subprocess. Mirrors TS
        # ``bridgeMain.ts:868-885``. The repl_bridge is single-session
        # so this triggers exactly when the active session's id
        # matches the inbound work; any other inbound work for the
        # at-capacity bridge is rejected by ``_poll_loop`` (which
        # short-circuits the poll when ``active_session is not None``).
        if (
            self.active_session is not None
            and self.active_session_id == session_id
        ):
            try:
                self.active_session.update_access_token(
                    secret.session_ingress_token,
                )
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    '[bridge:repl] update_access_token for existing '
                    'sessionId=%s failed: %s', session_id, err,
                )
            self.active_work_id = work_id
            if self.active_token_refresh is not None:
                try:
                    self.active_token_refresh.schedule(
                        session_id, secret.session_ingress_token,
                    )
                except Exception as err:  # noqa: BLE001
                    logger.debug(
                        '[bridge:repl] reschedule token refresh for '
                        'existing sessionId=%s failed: %s',
                        session_id, err,
                    )
            logger.info(
                '[bridge:repl] Updated access token for existing '
                'sessionId=%s workId=%s', session_id, work_id,
            )
            return

        # Phase 14c: dispatch v1 (session-ingress WS) and v2 (CCR)
        # work items both — the child SDK constructs its own
        # transport from sdk_url + access_token (via env vars set
        # in build_child_env). v1 work items used to be refused at
        # this site; that gate is now lifted.
        #
        # v1 / v2 use DIFFERENT URL sources:
        # * v2 (CCR) uses ``secret.api_base_url`` — the CCR control
        #   plane is the server-controlled endpoint and the secret
        #   carries the authoritative one for this work item.
        # * v1 (session-ingress) uses ``params.session_ingress_url``
        #   — the bridge's own configured ingress URL. Using
        #   ``secret.api_base_url`` would break proxy/tunnel setups
        #   where the secret's URL points to a remote that doesn't
        #   know about locally-created sessions (TS comment at
        #   ``bridgeMain.ts:905-907``; ``replBridge.ts:1471``).
        #
        # The auth split is also different: v1 session-ingress
        # accepts OAuth or JWT; v2 CCR /worker/* requires the JWT.
        # The Python parent always forwards the JWT (carried by
        # ``secret.session_ingress_token``) to the child as
        # ``CLAUDE_CODE_SESSION_ACCESS_TOKEN`` regardless of v1/v2;
        # the child runs the appropriate transport.
        use_ccr_v2 = bool(secret.use_code_sessions)
        # Phase 15: record the flag so the JWT refresh callback can
        # dispatch v1 (push-token-to-child) vs v2 (reconnect_session)
        # appropriately. Cleared in ``_await_session_done``.
        self.active_use_ccr_v2 = use_ccr_v2
        if use_ccr_v2:
            sdk_url = build_ccr_v2_sdk_url(secret.api_base_url, session_id)
        else:
            sdk_url = build_sdk_url(
                self.params.session_ingress_url, session_id,
            )
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
        # Phase 12c: the server may dispatch work for a session id we
        # didn't bootstrap with (e.g. after a server-side session
        # migration). Refresh the pointer so a crash mid-session
        # recovers into the right session, not the init bootstrap.
        await self._update_pointer(session_id=session_id)
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
        """Create a scheduler with v1/v2-aware ``on_refresh``.

        Phase 15: v1 and v2 diverge on how to refresh.

        * **v2 (CCR worker endpoints)**: CCR validates the JWT's
          ``session_id`` claim (TS ``register_worker.go:32``), so
          pushing an OAuth token to the child's stdin would break
          subsequent ``/worker/*`` requests. Instead, call
          ``api.reconnect_session(env_id, session_id)`` — the
          server re-dispatches the work item with a fresh JWT,
          which flows through the next poll's ``_process_work``.
        * **v1 (Session-Ingress)**: session-ingress accepts OAuth
          or JWT, so push the fresh OAuth token directly to the
          child via ``session.update_access_token``.

        Matches TS ``bridgeMain.ts:286-308``.
        """
        def on_refresh(session_id: str, fresh_token: str) -> None:
            if self.active_use_ccr_v2:
                # v2: schedule the async reconnect_session call.
                # ``on_refresh`` is sync but reconnect is async; fire
                # it as a task and don't await — the scheduler's
                # follow-up timer fires regardless.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    logger.warning(
                        '[bridge:repl] token refresh fired outside an '
                        'asyncio loop (sessionId=%s) — cannot dispatch '
                        'v2 reconnect', session_id,
                    )
                    return
                loop.create_task(
                    self._safe_reconnect_for_refresh(session_id),
                    name=f'bridge-repl-refresh-{session_id}',
                )
                return
            # v1: push fresh OAuth/JWT to child stdin.
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
            # OAuth token getter for the refresh chain. Phase 15:
            # returns the parent's current OAuth token, which the
            # scheduler then passes to ``on_refresh``. v1 pushes
            # this token to the child via stdin; v2 ignores the
            # token value and calls ``reconnect_session`` instead
            # (the token's value only matters to the v1 branch —
            # but the scheduler needs the call to return a non-None
            # value to fire ``on_refresh`` at all).
            return self.params.get_access_token()

        return TokenRefreshScheduler(
            get_access_token=get_access_token,
            on_refresh=on_refresh,
            label='repl-bridge',
        )

    async def _safe_reconnect_for_refresh(self, session_id: str) -> None:
        """v2 token-refresh helper. Calls ``api.reconnect_session``
        and swallows errors — the next refresh fire will retry.

        Mirrors TS ``bridgeMain.ts:295-305`` (``void
        api.reconnectSession(...).catch(...)``).
        """
        try:
            await self.api.reconnect_session(self.environment_id, session_id)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:repl] v2 token refresh via reconnect_session '
                'failed for sessionId=%s: %s', session_id, err,
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
        # pending refresh would write to a dead stdin (v1) or trigger
        # a reconnect for an archived session (v2).
        if self.active_token_refresh is not None:
            self.active_token_refresh.cancel_all()
            self.active_token_refresh = None
        # Phase 15: clear the v1/v2 flag so a future spawn starts
        # with a clean v1/v2 default until ``_process_work`` sets it.
        self.active_use_ccr_v2 = False
        # Stop the work item to free the server-side lease.
        await self._safe_stop_work(work_id, force=False)
        self.active_session = None
        self.active_work_id = None
        self.active_session_id = None
        # Phase 12c: the session just finished — clear ``session_id``
        # in the pointer so a crash before the next poll doesn't try
        # to resurrect an archived session. The pointer keeps its
        # bridge_id + env_id so the next start can still reuse the env.
        await self._update_pointer(session_id=None)

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

        # Phase 17: cancel the periodic pointer-mtime refresh task.
        # The loop also checks ``torn_down`` between sleeps, but
        # cancelling here ends it immediately instead of waiting up to
        # an hour for the next tick.
        if self.pointer_mtime_task is not None:
            self.pointer_mtime_task.cancel()
            try:
                await self.pointer_mtime_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self.pointer_mtime_task = None

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

        # Phase 12c: clean teardown → remove pointer. A future restart
        # should start fresh, not try to resurrect an env we just
        # deregistered. Best-effort; a leftover pointer is harmless
        # because read_pointer's host/dir staleness checks plus the
        # server's expiry will eventually drop it.
        await self._clear_pointer()


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

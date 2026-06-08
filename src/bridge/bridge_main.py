"""Multi-session bridge daemon — Phase 8 MVP slice.

Ports ``typescript/src/bridge/bridgeMain.ts`` (2991 lines in TS).

The TS file is the daemon/orchestrator entry point: parses CLI args,
registers a multi-session environment, runs a polling loop that spawns
session children up to capacity, manages per-session timeouts +
worktree directories + status display + heartbeat mode + two-track
error backoff + graceful shutdown.

For autonomous porting in one session, this module implements the
**structural skeleton + happy path**:

* ``parse_args(args)`` — flag parser (--verbose, --sandbox, --spawn,
  --capacity, --debug-file, --session-timeout, --permission-mode,
  --name, --help)
* ``BackoffConfig`` + ``DEFAULT_BACKOFF`` constants
* ``ParsedArgs`` dataclass
* ``BridgeHeadlessPermanentError`` exception (signals "stop trying;
  not a transient failure")
* ``is_connection_error(err)`` + ``is_server_error(err)`` predicates
* ``run_bridge_loop(config, env_id, env_secret, api, spawner, logger,
  cancel_event, ...)`` — multi-session work poll loop
* ``bridge_main(args, *, ...)`` — end-to-end daemon entry: parse →
  register → loop → shutdown

What is **explicitly deferred** with TODOs at the call sites:

* **Worktree spawn mode** — needs full ``git worktree`` integration
  (``createAgentWorktree`` / ``removeAgentWorktree``). The MVP accepts
  ``--spawn worktree`` but spawns sessions in the same dir with a
  warning.
* **Status display + UI** — no terminal renderer (no
  ``createBridgeLogger`` equivalent yet). A logger is plumbed through;
  output is logger-only.
* **Per-session timeout watchdog** — ``--session-timeout`` is parsed
  but not enforced. Phase 10 follow-up.
* **Heartbeat mode** (``non_exclusive_heartbeat_interval_ms > 0``) —
  defers to plain poll loop only.
* **Two-track error backoff** (connection vs general error tracks with
  independent give-up thresholds) — uses fixed sleep on errors.
* **KAIROS conditional logic** (resumable shutdown, --session-id /
  --continue resume) — flags rejected with a clear error.
* **Title derivation via onFirstUserMessage** — not wired (Phase 10).
* **Worktree analytics + spawn-mode display toggles** — out of scope.

What IS ported in full:

* CLI arg parsing for the common flag surface
* Multi-session capacity control (single + multi)
* Session bookkeeping (active_sessions, work_ids, compat_ids, timers
  maps for future expansion)
* Work poll loop with capacity gating
* stop_work retry on shutdown
* archive + deregister sequence
* Graceful shutdown signal handler
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.bridge.bridge_api import (
    BridgeFatalError,
    create_bridge_api_client,
    is_expired_error_type,
)
from src.bridge.poll_config_defaults import (
    DEFAULT_POLL_CONFIG,
    PollIntervalConfig,
)
from src.bridge.session_runner import (
    SessionSpawnerDeps,
    create_session_spawner,
)
from src.bridge.types import (
    BridgeApiClient,
    BridgeConfig,
    SessionHandle,
    SessionSpawnOpts,
    SessionSpawner,
    SpawnMode,
)
from src.bridge.work_secret import (
    build_ccr_v2_sdk_url,
    decode_work_secret,
)
from src.bridge.worktree import (
    WorktreePaths,
    create_agent_worktree,
    remove_agent_worktree,
)

logger = logging.getLogger(__name__)


# ── Backoff config (mirrors TS BackoffConfig + DEFAULT_BACKOFF) ──────────


@dataclass(frozen=True)
class BackoffConfig:
    """Backoff knobs for the poll loop.

    Mirrors TS ``BackoffConfig`` on ``bridgeMain.ts:53-66``. The MVP uses
    a much simpler fixed-interval strategy; this dataclass exists so the
    public surface matches TS and a future port can wire the full
    two-track backoff machinery.
    """

    conn_initial_ms: int = 2_000
    conn_cap_ms: int = 120_000
    conn_give_up_ms: int = 600_000
    general_initial_ms: int = 500
    general_cap_ms: int = 30_000
    general_give_up_ms: int = 600_000
    shutdown_grace_ms: int = 30_000
    stop_work_base_delay_ms: int = 1_000


DEFAULT_BACKOFF = BackoffConfig()


# ── Arg parsing ──────────────────────────────────────────────────────────


@dataclass
class ParsedArgs:
    """Output of ``parse_args``. Mirrors TS ``ParsedArgs`` on
    ``bridgeMain.ts:1694-1717``."""

    verbose: bool = False
    sandbox: bool = False
    debug_file: str | None = None
    session_timeout_ms: int | None = None
    permission_mode: str | None = None
    name: str | None = None
    spawn_mode: SpawnMode | None = None
    capacity: int | None = None
    create_session_in_dir: bool | None = None
    session_id: str | None = None
    continue_session: bool = False
    help: bool = False
    error: str | None = None


def _make_error(msg: str) -> ParsedArgs:
    return ParsedArgs(error=msg)


def _parse_spawn_value(raw: str | None) -> SpawnMode | str:
    if raw == 'session':
        return 'single-session'
    if raw == 'same-dir':
        return 'same-dir'
    if raw == 'worktree':
        return 'worktree'
    return (
        '--spawn requires one of: session, same-dir, worktree (got: '
        f'{raw or "<missing>"})'
    )


def _parse_capacity_value(raw: str | None) -> int | str:
    if raw is None:
        return '--capacity requires a positive integer (got: <missing>)'
    try:
        n = int(raw)
    except ValueError:
        return f'--capacity requires a positive integer (got: {raw})'
    if n < 1:
        return f'--capacity requires a positive integer (got: {raw})'
    return n


def parse_args(args: list[str]) -> ParsedArgs:
    """Parse command-line flags. Mirrors TS ``parseArgs`` on ``bridgeMain.ts:1736``.

    Supported flags (Phase 8 MVP):

    * ``--verbose`` / ``-v``
    * ``--sandbox`` / ``--no-sandbox``
    * ``--debug-file PATH`` (or ``--debug-file=PATH``)
    * ``--session-timeout SECONDS`` (or ``--session-timeout=SECONDS``)
    * ``--permission-mode MODE``
    * ``--name NAME``
    * ``--spawn {session,same-dir,worktree}``
    * ``--capacity N``
    * ``--create-session-in-dir`` / ``--no-create-session-in-dir``
    * ``--help`` / ``-h``

    KAIROS-only flags (``--session-id``, ``--continue`` / ``-c``) are
    explicitly rejected — those depend on perpetual mode which the MVP
    doesn't yet support.
    """
    out = ParsedArgs()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('--help', '-h'):
            out.help = True
        elif arg in ('--verbose', '-v'):
            out.verbose = True
        elif arg == '--sandbox':
            out.sandbox = True
        elif arg == '--no-sandbox':
            out.sandbox = False
        elif arg == '--debug-file' and i + 1 < len(args):
            i += 1
            out.debug_file = args[i]
        elif arg.startswith('--debug-file='):
            out.debug_file = arg[len('--debug-file='):]
        elif arg == '--session-timeout' and i + 1 < len(args):
            i += 1
            out.session_timeout_ms = _int_seconds_to_ms(args[i])
        elif arg.startswith('--session-timeout='):
            out.session_timeout_ms = _int_seconds_to_ms(
                arg[len('--session-timeout='):]
            )
        elif arg == '--permission-mode' and i + 1 < len(args):
            i += 1
            out.permission_mode = args[i]
        elif arg.startswith('--permission-mode='):
            out.permission_mode = arg[len('--permission-mode='):]
        elif arg == '--name' and i + 1 < len(args):
            i += 1
            out.name = args[i]
        elif arg.startswith('--name='):
            out.name = arg[len('--name='):]
        elif arg in ('--session-id', '-c', '--continue') or arg.startswith(
            '--session-id='
        ):
            return _make_error(
                f'{arg.split("=")[0]} is a KAIROS-only flag not yet '
                'supported in the MVP'
            )
        elif arg == '--spawn' or arg.startswith('--spawn='):
            if out.spawn_mode is not None:
                return _make_error('--spawn may only be specified once')
            raw: str | None
            if arg.startswith('--spawn='):
                raw = arg[len('--spawn='):]
            elif i + 1 < len(args):
                i += 1
                raw = args[i]
            else:
                raw = None
            v = _parse_spawn_value(raw)
            if isinstance(v, str) and v not in ('single-session', 'same-dir', 'worktree'):
                return _make_error(v)
            out.spawn_mode = v  # type: ignore[assignment]
        elif arg == '--capacity' or arg.startswith('--capacity='):
            if out.capacity is not None:
                return _make_error('--capacity may only be specified once')
            if arg.startswith('--capacity='):
                raw = arg[len('--capacity='):]
            elif i + 1 < len(args):
                i += 1
                raw = args[i]
            else:
                raw = None
            cv = _parse_capacity_value(raw)
            if isinstance(cv, int):
                out.capacity = cv
            else:
                return _make_error(cv)
        elif arg == '--create-session-in-dir':
            out.create_session_in_dir = True
        elif arg == '--no-create-session-in-dir':
            out.create_session_in_dir = False
        else:
            return _make_error(f'Unknown argument: {arg}')
        i += 1
    return out


def _int_seconds_to_ms(value: str) -> int:
    """Parse a positive-integer seconds value into ms."""
    try:
        n = int(value)
    except ValueError as err:
        raise ValueError(
            f'--session-timeout requires an integer (got: {value!r})'
        ) from err
    return n * 1000


def _now_ms() -> float:
    """Current wall-clock time in ms since the Unix epoch (float for
    sub-ms precision; backoff give-up math compares against it)."""
    return time.time() * 1000.0


# ── Error predicates ─────────────────────────────────────────────────────


def is_connection_error(err: Exception) -> bool:
    """True for transport-level errors (connection refused/reset, DNS, etc.).

    Mirrors TS ``isConnectionError`` on ``bridgeMain.ts:1589-1602``. Used
    by callers to choose the connection-error backoff track vs the
    general-error track.
    """
    import httpx

    return isinstance(
        err,
        (
            ConnectionError, ConnectionRefusedError, ConnectionResetError,
            ConnectionAbortedError, TimeoutError,
            socket.timeout, socket.gaierror,
            httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError,
        ),
    )


def is_server_error(err: Exception) -> bool:
    """True for 5xx errors (server-side failures, retryable)."""
    if isinstance(err, BridgeFatalError):
        return err.status >= 500
    return False


# ── Exceptions ───────────────────────────────────────────────────────────


class BridgeHeadlessPermanentError(Exception):
    """Signal that the daemon hit a permanent failure (don't retry).

    Mirrors TS ``BridgeHeadlessPermanentError`` on ``bridgeMain.ts:2773-2778``.
    Caller (e.g. ``runBridgeHeadless`` wrapper) propagates this so the
    supervising process can decide to back off vs exit.
    """


# ── Multi-session orchestrator ───────────────────────────────────────────


async def run_bridge_loop(
    config: BridgeConfig,
    environment_id: str,
    environment_secret: str,
    api: BridgeApiClient,
    spawner: SessionSpawner,
    cancel_event: asyncio.Event,
    *,
    backoff_config: BackoffConfig = DEFAULT_BACKOFF,
    initial_session_id: str | None = None,  # noqa: ARG001 future use
    poll_config: PollIntervalConfig = DEFAULT_POLL_CONFIG,
) -> None:
    """Run the multi-session work-poll loop.

    Mirrors TS ``runBridgeLoop`` on ``bridgeMain.ts:140``. The MVP
    implements:

    * Capacity-aware polling (up to ``config.max_sessions``)
    * Spawn → register → wait-done → stop_work per session
    * Graceful exit when ``cancel_event`` is set: SIGTERM all, wait up
      to ``backoff_config.shutdown_grace_ms``, SIGKILL stragglers
    * Best-effort stopWork on shutdown, with retry up to
      ``backoff_config.stop_work_base_delay_ms`` per attempt
    """
    daemon = _BridgeDaemon(
        config=config,
        environment_id=environment_id,
        environment_secret=environment_secret,
        api=api,
        spawner=spawner,
        cancel_event=cancel_event,
        backoff_config=backoff_config,
        poll_config=poll_config,
    )
    try:
        await daemon.run()
    finally:
        await daemon.shutdown()


@dataclass
class _BackoffTrack:
    """One exponential-backoff state machine.

    Mirrors TS's two-track pattern on ``bridgeMain.ts:1269-1399`` where
    connection errors and general errors each have an independent
    counter with separate cap + give-up thresholds. Calling ``fail()``
    increments the delay; ``reset()`` returns to ``initial_ms``.

    ``give_up_at_ms`` is set on the first failure and cleared on reset.
    The track is "given up" when wall-clock time exceeds it.
    """

    initial_ms: int
    cap_ms: int
    give_up_ms: int
    current_ms: int = 0
    give_up_at_ms: float | None = None

    def reset(self) -> None:
        self.current_ms = 0
        self.give_up_at_ms = None

    def fail(self, now_ms: float) -> int:
        """Record a failure; return the next sleep duration in ms.

        Doubles ``current_ms`` (capped at ``cap_ms``). On first failure,
        arms the give-up deadline.
        """
        if self.current_ms == 0:
            self.current_ms = self.initial_ms
            self.give_up_at_ms = now_ms + self.give_up_ms
        else:
            self.current_ms = min(self.current_ms * 2, self.cap_ms)
        return self.current_ms

    def is_given_up(self, now_ms: float) -> bool:
        return (
            self.give_up_at_ms is not None
            and now_ms >= self.give_up_at_ms
        )


class _BridgeDaemon:
    """Encapsulates the multi-session daemon state.

    Methods mirror the TS closure-heavy code structurally so the port
    is easy to audit.
    """

    def __init__(
        self,
        *,
        config: BridgeConfig,
        environment_id: str,
        environment_secret: str,
        api: BridgeApiClient,
        spawner: SessionSpawner,
        cancel_event: asyncio.Event,
        backoff_config: BackoffConfig,
        poll_config: PollIntervalConfig,
    ) -> None:
        self.config = config
        self.environment_id = environment_id
        self.environment_secret = environment_secret
        self.api = api
        self.spawner = spawner
        self.cancel_event = cancel_event
        self.backoff_config = backoff_config
        self.poll_config = poll_config

        # Per-session bookkeeping (matches TS active_sessions et al.)
        self.active_sessions: dict[str, SessionHandle] = {}
        self.session_work_ids: dict[str, str] = {}
        self.session_compat_ids: dict[str, str] = {}
        self.session_worktrees: dict[str, WorktreePaths] = {}
        self.completed_work_ids: set[str] = set()
        self.session_timer_tasks: dict[str, asyncio.Task[None]] = {}
        self.timed_out_sessions: set[str] = set()

        # Two-track exponential backoff state. Reset on any successful
        # poll (including empty-poll = no work).
        bc = backoff_config
        self._conn_track = _BackoffTrack(
            initial_ms=bc.conn_initial_ms,
            cap_ms=bc.conn_cap_ms,
            give_up_ms=bc.conn_give_up_ms,
        )
        self._general_track = _BackoffTrack(
            initial_ms=bc.general_initial_ms,
            cap_ms=bc.general_cap_ms,
            give_up_ms=bc.general_give_up_ms,
        )

    async def run(self) -> None:
        cfg = self.poll_config
        seek_interval = cfg.poll_interval_ms_not_at_capacity / 1000.0
        at_cap_interval = (
            cfg.poll_interval_ms_at_capacity / 1000.0
            if cfg.poll_interval_ms_at_capacity > 0
            else 60.0
        )
        heartbeat_interval = (
            cfg.non_exclusive_heartbeat_interval_ms / 1000.0
            if cfg.non_exclusive_heartbeat_interval_ms > 0
            else None
        )
        while not self.cancel_event.is_set():
            if len(self.active_sessions) >= self.config.max_sessions:
                # At capacity: optionally heartbeat active work items to
                # keep server-side leases alive between polls.
                if heartbeat_interval is not None:
                    await self._heartbeat_active_work()
                    await self._sleep_or_cancel(heartbeat_interval)
                else:
                    await self._sleep_or_cancel(at_cap_interval)
                continue
            try:
                work = await self.api.poll_for_work(
                    self.environment_id, self.environment_secret,
                )
            except BridgeFatalError as err:
                if is_expired_error_type(err.error_type) or err.status == 404:
                    logger.error(
                        '[bridge:main] Environment expired/lost '
                        '(MVP gives up): %s', err,
                    )
                    raise BridgeHeadlessPermanentError(str(err)) from err
                logger.error('[bridge:main] Poll fatal: %s', err)
                raise
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as err:  # noqa: BLE001
                # Two-track exponential backoff. Connection-class
                # errors get a longer initial delay (TCP/TLS handshake
                # cost) and an independent give-up timer.
                now_ms = _now_ms()
                track = (
                    self._conn_track
                    if is_connection_error(err)
                    else self._general_track
                )
                delay_ms = track.fail(now_ms)
                if track.is_given_up(now_ms):
                    label = (
                        'connection-error' if track is self._conn_track
                        else 'general-error'
                    )
                    logger.error(
                        '[bridge:main] %s track exceeded give-up '
                        'threshold (%sms): %s',
                        label, track.give_up_ms, err,
                    )
                    raise BridgeHeadlessPermanentError(
                        f'{label} give-up: {err}',
                    ) from err
                logger.warning(
                    '[bridge:main] Poll error (%s, sleeping %sms): %s',
                    'conn' if track is self._conn_track else 'general',
                    delay_ms, err,
                )
                await self._sleep_or_cancel(delay_ms / 1000.0)
                continue
            # Successful poll resets BOTH tracks — any prior error is
            # forgiven once we get a clean response.
            self._conn_track.reset()
            self._general_track.reset()
            if work is None:
                await self._sleep_or_cancel(seek_interval)
                continue
            await self._process_work(work)

    async def _heartbeat_active_work(self) -> None:
        """Send a lightweight heartbeat for each active work item.

        Mirrors TS heartbeat-mode behavior on ``bridgeMain.ts:649-730``.
        Best-effort: a single failure is logged but doesn't tear down
        the loop — the next poll-deadline tick will surface a permanent
        failure if the server has rejected the lease.
        """
        for session_id, work_id in list(self.session_work_ids.items()):
            session = self.active_sessions.get(session_id)
            if session is None:
                continue
            try:
                await self.api.heartbeat_work(
                    self.environment_id, work_id, session.access_token,
                )
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    '[bridge:main] heartbeat work_id=%s failed: %s',
                    work_id, err,
                )

    async def _process_work(self, work: dict[str, Any]) -> None:
        work_id = work.get('id')
        if not isinstance(work_id, str):
            return
        if work_id in self.completed_work_ids:
            # Stale redelivery — server hadn't yet processed stop_work.
            return
        data = work.get('data') or {}
        if not isinstance(data, dict):
            return
        work_type = data.get('type')
        if work_type == 'healthcheck':
            await self._safe_ack(work_id, self.environment_secret)
            return
        if work_type != 'session':
            return
        try:
            secret = decode_work_secret(work.get('secret') or '')
        except Exception as err:  # noqa: BLE001
            logger.error(
                '[bridge:main] decode_work_secret failed: %s', err
            )
            await self._safe_stop_work(work_id, force=True)
            return
        session_id = data.get('id')
        if not isinstance(session_id, str):
            return
        await self._safe_ack(work_id, secret.session_ingress_token)

        # MVP: CCR v2 only. v1 work (use_code_sessions = False) is rejected.
        use_ccr_v2 = bool(secret.use_code_sessions)
        if not use_ccr_v2:
            logger.warning(
                '[bridge:main] v1 session-ingress not supported in MVP'
            )
            await self._safe_stop_work(work_id, force=True)
            return
        sdk_url = build_ccr_v2_sdk_url(secret.api_base_url, session_id)
        spawn_opts: SessionSpawnOpts = {
            'session_id': session_id,
            'sdk_url': sdk_url,
            'access_token': secret.session_ingress_token,
            'use_ccr_v2': True,
            'worker_epoch': 0,  # MVP: full port fetches via /worker/register
        }
        worktree_paths: WorktreePaths | None = None
        working_dir = self.config.dir
        if self.config.spawn_mode == 'worktree':
            worktree_paths = await create_agent_worktree(
                self.config.dir, session_id,
            )
            working_dir = worktree_paths.working_dir
        try:
            session = self.spawner.spawn(spawn_opts, working_dir)
        except Exception as err:  # noqa: BLE001
            logger.error('[bridge:main] spawn failed: %s', err)
            if worktree_paths is not None:
                await remove_agent_worktree(worktree_paths)
            await self._safe_stop_work(work_id, force=True)
            return
        self.active_sessions[session_id] = session
        if worktree_paths is not None:
            self.session_worktrees[session_id] = worktree_paths
        self.session_work_ids[session_id] = work_id
        # session_compat_ids cached for future title/archive ops that
        # the MVP doesn't yet exercise — populated for forward compat.
        from src.bridge.session_id_compat import to_compat_session_id
        self.session_compat_ids[session_id] = to_compat_session_id(session_id)
        # Per-session timeout watchdog. ``--session-timeout SECONDS``
        # → ``config.session_timeout_ms`` (or None to disable). Mirrors
        # TS watchdog on ``bridgeMain.ts:1177-1192`` / ``1677-1692``.
        # When the timer fires, ``timed_out_sessions`` is marked so
        # ``_on_session_done`` can distinguish timeout from clean exit
        # in logs / future telemetry.
        if self.config.session_timeout_ms:
            timer_task = asyncio.create_task(
                self._session_timeout_watchdog(
                    session_id,
                    self.config.session_timeout_ms / 1000.0,
                ),
                name=f'bridge-timer-{session_id}',
            )
            self.session_timer_tasks[session_id] = timer_task
        logger.info(
            '[bridge:main] Spawned session_id=%s work_id=%s '
            '(%s/%s active)',
            session_id, work_id,
            len(self.active_sessions), self.config.max_sessions,
        )
        # Fire-and-forget wait-done.
        asyncio.create_task(
            self._on_session_done(session_id),
            name=f'bridge-await-{session_id}',
        )

    async def _session_timeout_watchdog(
        self, session_id: str, timeout_seconds: float,
    ) -> None:
        """Kill the session after ``timeout_seconds`` if still active.

        Mirrors TS ``onSessionTimeout`` semantics. The session's
        ``wait_done`` will then resolve with whatever status the kill
        produced (typically 'interrupted'); ``_on_session_done`` checks
        ``timed_out_sessions`` to log this distinctly.
        """
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return
        session = self.active_sessions.get(session_id)
        if session is None:
            return
        logger.warning(
            '[bridge:main] Session %s exceeded timeout %.1fs — killing',
            session_id, timeout_seconds,
        )
        self.timed_out_sessions.add(session_id)
        try:
            session.kill()
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:main] watchdog kill failed: %s', err
            )

    async def _on_session_done(self, session_id: str) -> None:
        session = self.active_sessions.get(session_id)
        if session is None:
            return
        try:
            status = await session.wait_done()
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:main] wait_done(%s) raised: %s', session_id, err
            )
            status = 'failed'
        # Cancel any pending timeout watchdog — session is done.
        timer = self.session_timer_tasks.pop(session_id, None)
        if timer is not None and not timer.done():
            timer.cancel()
        work_id = self.session_work_ids.get(session_id)
        if work_id is not None:
            await self._safe_stop_work(work_id, force=False)
            self.completed_work_ids.add(work_id)
        self.active_sessions.pop(session_id, None)
        self.session_work_ids.pop(session_id, None)
        self.session_compat_ids.pop(session_id, None)
        worktree_paths = self.session_worktrees.pop(session_id, None)
        if worktree_paths is not None:
            await remove_agent_worktree(worktree_paths)
        # ``discard`` doesn't return a value; check membership first.
        was_timeout = session_id in self.timed_out_sessions
        self.timed_out_sessions.discard(session_id)
        timeout_marker = ' (TIMEOUT)' if was_timeout else ''
        logger.info(
            '[bridge:main] Session done session_id=%s status=%s%s',
            session_id, status, timeout_marker,
        )

    async def shutdown(self) -> None:
        """SIGTERM all sessions, wait up to ``shutdown_grace_ms``, SIGKILL
        stragglers, stop_work + deregister.

        Mirrors TS shutdown sequence on ``bridgeMain.ts:1402-1577``.
        Idempotent — safe to call multiple times.
        """
        active_snapshot = list(self.active_sessions.values())
        work_id_snapshot = dict(self.session_work_ids)

        # Cancel any pending per-session timeout watchdogs so they
        # don't fire mid-shutdown and inject confusing log lines.
        for timer in self.session_timer_tasks.values():
            if not timer.done():
                timer.cancel()
        self.session_timer_tasks.clear()

        # SIGTERM all.
        for session in active_snapshot:
            try:
                session.kill()
            except Exception as err:  # noqa: BLE001
                logger.warning('[bridge:main] kill failed: %s', err)

        # Wait up to shutdown_grace_ms for the children to exit.
        if active_snapshot:
            grace = self.backoff_config.shutdown_grace_ms / 1000.0
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *(s.wait_done() for s in active_snapshot),
                        return_exceptions=True,
                    ),
                    timeout=grace,
                )
            except asyncio.TimeoutError:
                # SIGKILL stragglers.
                for session in active_snapshot:
                    try:
                        session.force_kill()
                    except Exception as err:  # noqa: BLE001
                        logger.warning(
                            '[bridge:main] force_kill failed: %s', err
                        )

        # Stop all outstanding work items.
        for work_id in work_id_snapshot.values():
            await self._safe_stop_work(work_id, force=True)

        # Remove any session worktrees that did not go through the normal
        # wait-done cleanup path during shutdown.
        worktree_snapshot = list(self.session_worktrees.values())
        self.session_worktrees.clear()
        for worktree_paths in worktree_snapshot:
            await remove_agent_worktree(worktree_paths)

        # Deregister the environment (best-effort).
        try:
            await self.api.deregister_environment(self.environment_id)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:main] deregister_environment failed: %s', err
            )

    async def _safe_ack(self, work_id: str, session_token: str) -> None:
        try:
            await self.api.acknowledge_work(
                self.environment_id, work_id, session_token,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:main] ack failed work_id=%s: %s', work_id, err
            )

    async def _safe_stop_work(self, work_id: str, *, force: bool) -> None:
        try:
            await self.api.stop_work(
                self.environment_id, work_id, force,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning(
                '[bridge:main] stop_work failed work_id=%s: %s',
                work_id, err,
            )

    async def _sleep_or_cancel(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.cancel_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass


# ── End-to-end entry point ──────────────────────────────────────────────


async def bridge_main(
    args: list[str],
    *,
    api: BridgeApiClient | None = None,
    spawner: SessionSpawner | None = None,
    get_access_token: Callable[[], str | None] = lambda: 'tok-placeholder',
    runner_version: str = 'py-bridge-mvp',
    base_url: str = 'https://api.anthropic.com',
    machine_name: str = 'localhost',
    branch: str = 'main',
    git_repo_url: str | None = None,
    working_dir: str = '.',
    cancel_event: asyncio.Event | None = None,
) -> int:
    """End-to-end daemon entry: parse → register → run loop → shutdown.

    Returns a process exit code: 0 = clean shutdown, 1 = parse error /
    help, 2 = registration failed, 3 = permanent runtime error.

    Test seams:

    * ``api`` / ``spawner``: pre-built for tests.
    * ``get_access_token``: OAuth token getter.
    * ``cancel_event``: optional ``asyncio.Event`` so tests can ask the
      daemon to shut down without sending a real signal.
    """
    parsed = parse_args(args)
    if parsed.error is not None:
        logger.error('[bridge:main] %s', parsed.error)
        return 1
    if parsed.help:
        _print_usage()
        return 0

    spawn_mode = parsed.spawn_mode or 'single-session'
    capacity = parsed.capacity or (
        1 if spawn_mode == 'single-session' else 4
    )

    bridge_config = BridgeConfig(
        dir=working_dir,
        machine_name=machine_name,
        branch=branch,
        git_repo_url=git_repo_url,
        max_sessions=capacity,
        spawn_mode=spawn_mode,
        verbose=parsed.verbose,
        sandbox=parsed.sandbox,
        bridge_id=str(uuid.uuid4()),
        worker_type='claude_code',
        environment_id='',  # filled by registration
        api_base_url=base_url,
        session_ingress_url=base_url,
        debug_file=parsed.debug_file,
        session_timeout_ms=parsed.session_timeout_ms,
    )

    if api is None:
        api = create_bridge_api_client(
            base_url=base_url,
            get_access_token=get_access_token,
            runner_version=runner_version,
        )

    try:
        registration = await api.register_bridge_environment(bridge_config)
    except BridgeFatalError as err:
        logger.error('[bridge:main] Registration failed: %s', err)
        return 2
    environment_id = registration['environment_id']
    environment_secret = registration['environment_secret']
    logger.info(
        '[bridge:main] Registered environment_id=%s capacity=%s mode=%s',
        environment_id, capacity, spawn_mode,
    )

    if spawner is None:
        spawner = create_session_spawner(SessionSpawnerDeps(
            exec_path='claude',
            verbose=parsed.verbose,
            sandbox=parsed.sandbox,
            debug_file=parsed.debug_file,
            permission_mode=parsed.permission_mode,
        ))

    if cancel_event is None:
        cancel_event = asyncio.Event()
        _install_signal_handlers(cancel_event)

    try:
        await run_bridge_loop(
            bridge_config,
            environment_id,
            environment_secret,
            api,
            spawner,
            cancel_event,
        )
    except BridgeHeadlessPermanentError as err:
        logger.error('[bridge:main] Permanent error: %s', err)
        return 3
    return 0


def _install_signal_handlers(cancel_event: asyncio.Event) -> None:
    """Register SIGINT/SIGTERM handlers that set ``cancel_event``.

    No-op on platforms where ``loop.add_signal_handler`` isn't available
    (notably Windows). The MVP tolerates that by relying on the test
    seam ``cancel_event`` instead.
    """
    import sys

    if sys.platform == 'win32':
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, cancel_event.set)
        except (NotImplementedError, RuntimeError):
            pass


def _print_usage() -> None:
    """Print a minimal usage banner. Mirrors TS help text shape."""
    usage = """Usage: claude remote-control [options]

Options:
  --verbose, -v               Enable verbose logging
  --sandbox                   Run children in sandbox
  --no-sandbox                Disable sandbox (default)
  --debug-file PATH           Write per-session debug log
  --session-timeout SECONDS   Per-session timeout (parsed but not yet enforced)
  --permission-mode MODE      Default permission mode for children
  --name NAME                 Friendly name for the registered environment
  --spawn {session,same-dir,worktree}
                              Spawn mode (worktree mode logs a warning)
  --capacity N                Max concurrent sessions (default 1 or 4)
  --create-session-in-dir     Override default session-in-dir behavior
  --no-create-session-in-dir  Disable session-in-dir behavior
  --help, -h                  Show this help

Note: --session-id / --continue (perpetual mode) are not yet supported.
"""
    print(usage)


__all__ = [
    'BackoffConfig',
    'BridgeHeadlessPermanentError',
    'DEFAULT_BACKOFF',
    'ParsedArgs',
    'bridge_main',
    'is_connection_error',
    'is_server_error',
    'parse_args',
    'run_bridge_loop',
]

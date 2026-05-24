"""Env-less Remote Control bridge core — Phase 5 MVP.

Ports ``typescript/src/bridge/remoteBridgeCore.ts``.

"Env-less" means no Environments API layer — this connects directly to
the session-ingress (CCR v2) layer:

  1. POST ``/v1/code/sessions``              → ``cse_*`` session id
  2. POST ``/v1/code/sessions/{id}/bridge``  → worker JWT + epoch + TTL
  3. ``create_v2_repl_transport``            → SSE reads + CCRClient writes
  4. ``TokenRefreshScheduler``               → proactive ``/bridge`` re-call before expiry
  5. 401 on SSE                              → re-fetch ``/bridge`` and rebuild transport

No register/poll/ack/stop/heartbeat/deregister environment lifecycle.
Each ``/bridge`` call bumps ``worker_epoch`` server-side, so any refresh
path must rebuild the transport (a JWT-only swap leaves the old
CCRClient heartbeating with a stale epoch → 409 within 20s).

**MVP scope** (this Phase 5 port intentionally defers a few things):

* **No connect-timeout telemetry** — TS arms a ``setTimeout`` that
  emits ``tengu_bridge_repl_connect_timeout`` if neither onConnect nor
  onClose fires before ``cfg.connect_timeout_ms``. The Python port logs
  the deadline as a debug warning instead; analytics wiring lands in
  Phase 10.
* **No CCR mirror-mode telemetry** — the ``CCR_MIRROR`` feature flag
  branches on telemetry event names; we route everything through
  ``tengu_bridge_repl_*`` for now.
* **Trusted-device token** — passes ``None`` (no Phase 10 keychain).
* **``ConnectCause`` enum** — kept as a string for log clarity but not
  used for telemetry discriminator (no analytics in this build).

What IS ported in full:

* OAuth → ``/code/sessions`` → ``/bridge`` init with retry+jitter
* v2 transport build (SSE + CCRClient via existing factory)
* FlushGate + dual-set UUID dedup (echo + re-delivery)
* Proactive JWT refresh via ``TokenRefreshScheduler`` (epoch-bumping
  rebuild on fire)
* 401 SSE recovery (token refresh + rebuild)
* ``ReplBridgeHandle``-style return surface: ``write_messages``,
  ``write_sdk_messages``, ``send_control_request``,
  ``send_control_response``, ``send_cancel_request``, ``send_result``,
  ``teardown``
* Idempotent teardown: cancel scheduler → drop gate → reportState idle →
  write result → archive (with 401 retry) → close
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from src.bridge.bounded_uuid_set import BoundedUUIDSet
from src.bridge.code_session_api import (
    RemoteCredentials,
    create_code_session,
    fetch_remote_credentials,
)
from src.bridge.env_less_bridge_config import (
    DEFAULT_ENV_LESS_BRIDGE_CONFIG,
    EnvLessBridgeConfig,
    get_env_less_bridge_config,
)
from src.bridge.flush_gate import FlushGate
from src.bridge.jwt_utils import TokenRefreshScheduler
from src.bridge.messaging import (
    extract_title_text,
    handle_ingress_message,
    is_eligible_bridge_message,
    make_result_message,
)
from src.bridge.messaging_handlers import (
    ServerControlRequestHandlers,
    handle_server_control_request,
)
from src.bridge.repl_bridge_transport import (
    ReplBridgeTransport,
    V2TransportOptions,
    create_v2_repl_transport,
)
from src.bridge.session_id_compat import to_compat_session_id
from src.bridge.work_secret import build_ccr_v2_sdk_url
from src.types.messages import Message
from src.utils.message_mappers import to_sdk_messages

logger = logging.getLogger(__name__)


_TEARDOWN_RESULT_WRITE_TIMEOUT_SECONDS = 0.5
"""Cap on the teardown result-write enqueue wait. ``gracefulShutdown``
races teardown against a 2s budget; 0.5s leaves headroom for archive +
close while still containing back-pressure stalls."""


# ── Public types ──────────────────────────────────────────────────────────


BridgeState = str
"""Lifecycle states emitted via ``on_state_change``: ``'ready'``,
``'connected'``, ``'reconnecting'``, ``'failed'``. Kept as ``str`` to
stay compatible with Phase 6+ orchestrators that share the same union.
"""


OnInboundMessage = Callable[[dict[str, Any]], Any]
OnUserMessage = Callable[[str, str], bool]
OnPermissionResponse = Callable[[dict[str, Any]], None]
OnInterrupt = Callable[[], None]
OnSetModel = Callable[[str | None], None]
OnSetMaxThinkingTokens = Callable[[int | None], None]
OnSetPermissionMode = Callable[[str], Any]
OnStateChange = Callable[..., None]
"""``on_state_change(state, detail=None)`` — kept as ``...`` so call
sites can omit detail."""

OnAuth401 = Callable[[str], Awaitable[bool]]
GetAccessToken = Callable[[], str | None]


@dataclass
class EnvLessBridgeParams:
    """Configuration for ``init_env_less_bridge_core``.

    Mirrors TS ``EnvLessBridgeParams`` on ``remoteBridgeCore.ts:89-131``.
    Required: ``base_url``, ``org_uuid``, ``title``, ``get_access_token``,
    ``initial_history_cap``. All callbacks are optional.
    """

    base_url: str
    org_uuid: str
    title: str
    get_access_token: GetAccessToken
    initial_history_cap: int
    initial_messages: list[Message] | None = None
    on_auth_401: OnAuth401 | None = None
    on_inbound_message: OnInboundMessage | None = None
    on_user_message: OnUserMessage | None = None
    on_permission_response: OnPermissionResponse | None = None
    on_interrupt: OnInterrupt | None = None
    on_set_model: OnSetModel | None = None
    on_set_max_thinking_tokens: OnSetMaxThinkingTokens | None = None
    on_set_permission_mode: OnSetPermissionMode | None = None
    on_state_change: OnStateChange | None = None
    outbound_only: bool = False
    tags: list[str] | None = None


@dataclass
class RemoteBridgeHandle:
    """Opaque handle returned by ``init_env_less_bridge_core``.

    Mirrors the consumer-facing surface of TS ``ReplBridgeHandle``
    (``remoteBridgeCore.ts:763-886``). All write methods are sync
    fire-and-forget — the underlying transport batches writes via
    ``SerialBatchEventUploader``. ``teardown`` is async and idempotent.
    """

    bridge_session_id: str
    environment_id: str  # always empty for env-less
    session_ingress_url: str
    write_messages: Callable[[list[Message]], None]
    write_sdk_messages: Callable[[list[dict[str, Any]]], None]
    send_control_request: Callable[[dict[str, Any]], None]
    send_control_response: Callable[[dict[str, Any]], None]
    send_cancel_request: Callable[[str], None]
    send_result: Callable[[], None]
    teardown: Callable[[], Awaitable[None]]


# ── Init ──────────────────────────────────────────────────────────────────


async def init_env_less_bridge_core(
    params: EnvLessBridgeParams,
    *,
    http_client: httpx.AsyncClient | None = None,
    config: EnvLessBridgeConfig | None = None,
    transport_factory: Callable[
        [V2TransportOptions], Awaitable[ReplBridgeTransport]
    ] | None = None,
) -> RemoteBridgeHandle | None:
    """Create a session, fetch a worker JWT, connect the v2 transport.

    Returns ``None`` on any pre-flight failure (session create failed,
    ``/bridge`` failed, transport setup failed). Caller surfaces this as
    a generic "initialization failed" state.

    Test seams (kw-only — production callers omit):

    * ``http_client``: optional injected ``httpx.AsyncClient`` for the
      ``/code/sessions``, ``/bridge``, and ``archive`` calls.
    * ``config``: override ``EnvLessBridgeConfig`` (otherwise fetched
      via ``get_env_less_bridge_config()`` which currently returns
      defaults).
    * ``transport_factory``: override the v2 transport constructor for
      tests so they can inject a fake without hitting the SSE/CCR layer.
    """
    cfg = config if config is not None else await get_env_less_bridge_config()
    factory = transport_factory or create_v2_repl_transport

    # ── 1. OAuth pre-check ─────────────────────────────────────────────
    access_token = params.get_access_token()
    if not access_token:
        logger.debug('[remote-bridge] No OAuth token')
        return None

    # ── 2. Create session (POST /v1/code/sessions) ─────────────────────
    timeout_seconds = cfg.http_timeout_ms / 1000.0
    session_id = await _with_retry(
        lambda: create_code_session(
            params.base_url,
            access_token,
            params.title,
            timeout_seconds=timeout_seconds,
            tags=params.tags,
            client=http_client,
        ),
        'createCodeSession',
        cfg,
    )
    if session_id is None:
        _fire_state(params.on_state_change, 'failed',
                    'Session creation failed — see debug log')
        return None
    logger.debug('[remote-bridge] Created session %s', session_id)

    # ── 3. Fetch bridge credentials ────────────────────────────────────
    credentials = await _with_retry(
        lambda: fetch_remote_credentials(
            session_id,
            params.base_url,
            access_token,
            timeout_seconds=timeout_seconds,
            client=http_client,
        ),
        'fetchRemoteCredentials',
        cfg,
    )
    if credentials is None:
        _fire_state(params.on_state_change, 'failed',
                    'Remote credentials fetch failed — see debug log')
        await _safe_archive_session(
            session_id,
            params.base_url,
            access_token,
            params.org_uuid,
            timeout_seconds,
            http_client,
        )
        return None
    logger.debug(
        '[remote-bridge] Fetched bridge credentials (expires_in=%ss)',
        credentials.expires_in,
    )

    # ── 4. Build v2 transport ──────────────────────────────────────────
    session_url = build_ccr_v2_sdk_url(credentials.api_base_url, session_id)
    try:
        transport = await factory(
            V2TransportOptions(
                session_url=session_url,
                ingress_token=credentials.worker_jwt,
                session_id=session_id,
                epoch=credentials.worker_epoch,
                heartbeat_interval_seconds=cfg.heartbeat_interval_ms / 1000.0,
                heartbeat_jitter_fraction=cfg.heartbeat_jitter_fraction,
                outbound_only=params.outbound_only,
                # Per-instance closure — keeps the worker JWT out of
                # ``CLAUDE_CODE_SESSION_ACCESS_TOKEN`` env which mcp/client
                # would otherwise leak to user-configured MCP servers.
                # Frozen at construction: transport is fully rebuilt on
                # refresh (rebuild_transport below) with a fresh closure.
                get_auth_token=_freeze_token(credentials.worker_jwt),
            )
        )
    except Exception as err:  # noqa: BLE001  surface as pre-flight failure
        logger.error('[remote-bridge] v2 transport setup failed: %s', err)
        _fire_state(params.on_state_change, 'failed',
                    f'Transport setup failed: {err}')
        await _safe_archive_session(
            session_id,
            params.base_url,
            access_token,
            params.org_uuid,
            timeout_seconds,
            http_client,
        )
        return None
    logger.debug(
        '[remote-bridge] v2 transport created (epoch=%s)',
        credentials.worker_epoch,
    )
    _fire_state(params.on_state_change, 'ready')

    # ── 5. State (closures shared by all callbacks) ────────────────────
    state = _BridgeState(
        params=params,
        cfg=cfg,
        session_id=session_id,
        credentials=credentials,
        transport=transport,
        http_client=http_client,
        transport_factory=factory,
    )

    # ── 6. JWT refresh scheduler ───────────────────────────────────────
    refresh = TokenRefreshScheduler(
        get_access_token=_async_refresh_token(params),
        on_refresh=state.on_jwt_refresh,
        label='remote',
        refresh_buffer_ms=cfg.token_refresh_buffer_ms,
    )
    state.refresh = refresh
    refresh.schedule_from_expires_in(session_id, credentials.expires_in)

    # ── 7. Wire transport callbacks ────────────────────────────────────
    state.wire_transport_callbacks()

    # Start the flushGate BEFORE connect *unconditionally* so any
    # write_messages() / send_* calls that arrive during the handshake
    # are queued instead of dropped. The Python ``CCRClient.write_event``
    # silently drops messages while ``_initialized is False`` (unlike
    # TS's ``SerialBatchEventUploader`` which queues), so any pre-onConnect
    # write would be lost without this gate. The gate is drained in
    # ``_on_connect`` once the new transport's CCR is initialized.
    state.flush_gate.start()
    transport.connect()

    # ── 8. Return handle ───────────────────────────────────────────────
    return RemoteBridgeHandle(
        bridge_session_id=session_id,
        environment_id='',
        session_ingress_url=credentials.api_base_url,
        write_messages=state.write_messages,
        write_sdk_messages=state.write_sdk_messages,
        send_control_request=state.send_control_request,
        send_control_response=state.send_control_response,
        send_cancel_request=state.send_cancel_request,
        send_result=state.send_result,
        teardown=state.teardown,
    )


# ── Internal state machine (one instance per bridge) ──────────────────────


@dataclass
class _BridgeState:
    """All mutable state for one env-less bridge.

    Lives in its own class so the closure-heavy TS code maps cleanly to
    Python — each "inner function" in TS becomes a method that reads/
    writes ``self``.
    """

    params: EnvLessBridgeParams
    cfg: EnvLessBridgeConfig
    session_id: str
    credentials: RemoteCredentials
    transport: ReplBridgeTransport
    http_client: httpx.AsyncClient | None
    transport_factory: Callable[
        [V2TransportOptions], Awaitable[ReplBridgeTransport]
    ]
    refresh: TokenRefreshScheduler | None = None

    recent_posted_uuids: BoundedUUIDSet = field(init=False)
    initial_message_uuids: set[str] = field(default_factory=set)
    recent_inbound_uuids: BoundedUUIDSet = field(init=False)
    flush_gate: FlushGate[Message] = field(default_factory=FlushGate)

    initial_flush_done: bool = False
    torn_down: bool = False
    auth_recovery_in_flight: bool = False
    user_message_callback_done: bool = False
    connect_cause: str = 'initial'

    def __post_init__(self) -> None:
        # UUID dedup ring buffers, sized per env-less config.
        self.recent_posted_uuids = BoundedUUIDSet(
            self.cfg.uuid_dedup_buffer_size
        )
        self.recent_inbound_uuids = BoundedUUIDSet(
            self.cfg.uuid_dedup_buffer_size
        )

        # Seed dedup with initial-history UUIDs so server echoes of the
        # flushed history are recognized as our own.
        if self.params.initial_messages:
            for msg in self.params.initial_messages:
                self.initial_message_uuids.add(msg.uuid)
                self.recent_posted_uuids.add(msg.uuid)

        # Latch onUserMessage as already-done when no callback was wired.
        self.user_message_callback_done = self.params.on_user_message is None

    # ── Callback wiring ────────────────────────────────────────────────

    def wire_transport_callbacks(self) -> None:
        """Wire SSE setOnConnect / setOnData / setOnClose callbacks.

        Re-callable after a transport rebuild — captures ``self.transport``
        lazily via the closures so the new transport receives the wiring.
        """
        active_transport = self.transport
        self.transport.set_on_connect(
            lambda: self._on_connect(active_transport)
        )
        self.transport.set_on_data(self._on_data)
        self.transport.set_on_close(self._on_close)

    def _on_connect(self, flush_transport: ReplBridgeTransport) -> None:
        """Fired when the transport handshake completes (CCR initialized).

        At this point ``CCRClient.is_initialized`` is True (the v2 transport
        wires this callback after ``await ccr.initialize()`` resolves), so
        it's safe to drain the flushGate — any queued writes will reach the
        uploader and be POSTed. Flushes initial history first if present.

        ``flush_transport`` is captured at wire time — if a 401/teardown
        swaps ``self.transport`` mid-flush, the stale callback is a no-op
        (matches TS guard pattern).
        """
        logger.debug('[remote-bridge] v2 transport connected')
        if (
            not self.initial_flush_done
            and self.params.initial_messages
            and len(self.params.initial_messages) > 0
        ):
            self.initial_flush_done = True
            asyncio.create_task(
                self._flush_history_then_drain(flush_transport)
            )
            return
        # No initial flush — drain any pre-connect queued writes
        # (``write_messages`` calls that landed during the handshake) and
        # close the gate so subsequent writes go straight to the uploader.
        if self.flush_gate.active:
            self._drain_flush_gate()
        _fire_state(self.params.on_state_change, 'connected')

    async def _flush_history_then_drain(
        self, flush_transport: ReplBridgeTransport
    ) -> None:
        """Async wrapper for flush + drain. Mirrors TS .finally() chain.

        Stale-transport guards: if the transport was swapped mid-flush
        (401 recovery, teardown), skip the drain — the new transport's
        re-wired onConnect will re-flush.
        """
        try:
            assert self.params.initial_messages is not None
            await self._flush_history(self.params.initial_messages)
        except Exception as err:  # noqa: BLE001  log + carry on
            logger.warning('[remote-bridge] flushHistory failed: %s', err)
        finally:
            if (
                self.transport is flush_transport
                and not self.torn_down
                and not self.auth_recovery_in_flight
            ):
                self._drain_flush_gate()
                _fire_state(self.params.on_state_change, 'connected')

    def _on_data(self, data: str) -> None:
        """Route an SSE event line to the ingress handler."""
        params = self.params
        transport = self.transport

        on_permission_response_wrapped: OnPermissionResponse | None = None
        if params.on_permission_response is not None:
            inner = params.on_permission_response

            def wrapped(response: dict[str, Any]) -> None:
                # Remote client answered the prompt — turn resumes.
                # Without reportState('running'), the server stays on
                # ``requires_action`` until the next user message or
                # turn-end result.
                transport.report_state({'state': 'running'})
                inner(response)

            on_permission_response_wrapped = wrapped

        def on_control_request(request: dict[str, Any]) -> None:
            handle_server_control_request(
                request,
                ServerControlRequestHandlers(
                    transport=transport,  # type: ignore[arg-type]
                    session_id=self.session_id,
                    outbound_only=params.outbound_only,
                    on_interrupt=params.on_interrupt,
                    on_set_model=params.on_set_model,
                    on_set_max_thinking_tokens=params.on_set_max_thinking_tokens,
                    on_set_permission_mode=params.on_set_permission_mode,
                ),
            )

        handle_ingress_message(
            data,
            self.recent_posted_uuids,
            self.recent_inbound_uuids,
            params.on_inbound_message,
            on_permission_response_wrapped,
            on_control_request,
        )

    def _on_close(self, code: int | None) -> None:
        """Fired when the transport closes terminally.

        Per TS comment: onClose fires only for TERMINAL failures —
        401 (JWT invalid), 4090 (epoch mismatch), 4091 (init failed),
        or SSE reconnect-budget exhausted. Transient drops are handled
        inside SSETransport. 401 is recoverable (fetch fresh JWT,
        rebuild transport); other codes are dead-ends.
        """
        if self.torn_down:
            return
        logger.debug('[remote-bridge] v2 transport closed (code=%s)', code)
        if code == 401 and not self.auth_recovery_in_flight:
            asyncio.create_task(self._recover_from_auth_failure())
            return
        _fire_state(
            self.params.on_state_change, 'failed',
            f'Transport closed (code {code})',
        )

    # ── Transport rebuild (shared by proactive refresh + 401 recovery) ──

    async def _rebuild_transport(
        self,
        fresh: RemoteCredentials,
        cause: str,
    ) -> None:
        """Replace the transport with a fresh JWT+epoch.

        Caller MUST set ``self.auth_recovery_in_flight = True`` before
        calling (synchronously, before any ``await``) and clear it in a
        ``finally``. Moving that here would be too late to prevent a
        double ``/bridge`` fetch.
        """
        self.connect_cause = cause
        # Queue writes during rebuild — once /bridge returns, the old
        # transport's epoch is stale and its next write/heartbeat 409s.
        # ``deactivate()`` (vs ``drop()``) on success leaves queued items
        # for the new transport's ``_on_connect`` to drain.
        self.flush_gate.start()
        success = False
        try:
            old_seq = self.transport.get_last_sequence_num()
            self.transport.close()
            self.transport = await self.transport_factory(
                V2TransportOptions(
                    session_url=build_ccr_v2_sdk_url(
                        fresh.api_base_url, self.session_id
                    ),
                    ingress_token=fresh.worker_jwt,
                    session_id=self.session_id,
                    epoch=fresh.worker_epoch,
                    heartbeat_interval_seconds=(
                        self.cfg.heartbeat_interval_ms / 1000.0
                    ),
                    heartbeat_jitter_fraction=(
                        self.cfg.heartbeat_jitter_fraction
                    ),
                    initial_sequence_num=old_seq,
                    outbound_only=self.params.outbound_only,
                    get_auth_token=_freeze_token(fresh.worker_jwt),
                )
            )
            if self.torn_down:
                # Teardown fired during the async factory window — don't
                # wire/connect/schedule; we'd re-arm timers after cancelAll
                # and fire onInboundMessage into a torn-down bridge.
                self.transport.close()
                return
            self.wire_transport_callbacks()
            self.transport.connect()
            if self.refresh is not None:
                self.refresh.schedule_from_expires_in(
                    self.session_id, fresh.expires_in
                )
            self.credentials = fresh
            # Leave the gate active — the new transport's ``_on_connect``
            # will drain it once CCR.initialize() resolves. Python
            # ``CCRClient.write_event`` silently drops while
            # ``_initialized is False`` (TS ``SerialBatchEventUploader``
            # queues instead — known divergence), so draining here would
            # lose every queued message between now and the SSE/CCR
            # handshake completing.
            success = True
        finally:
            # Failure path (exception unwound the try, or torn_down
            # short-circuit): drop the queued items because the new
            # transport isn't going to come up.
            if not success:
                self.flush_gate.drop()

    # ── 401 recovery ───────────────────────────────────────────────────

    async def _recover_from_auth_failure(self) -> None:
        """Recover from a 401 on the SSE stream.

        Refresh OAuth, re-fetch ``/bridge``, rebuild transport. Shared
        ``auth_recovery_in_flight`` flag with the proactive refresh path
        prevents double-bumping epoch.
        """
        if self.auth_recovery_in_flight:
            return
        self.auth_recovery_in_flight = True
        _fire_state(
            self.params.on_state_change, 'reconnecting',
            'JWT expired — refreshing',
        )
        logger.debug('[remote-bridge] 401 on SSE — attempting JWT refresh')
        try:
            # getAccessToken() returns expired tokens as non-None strings
            # — unconditional OAuth refresh below catches that.
            stale = self.params.get_access_token()
            if self.params.on_auth_401 is not None:
                await self.params.on_auth_401(stale or '')
            oauth_token = self.params.get_access_token() or stale
            if not oauth_token or self.torn_down:
                if not self.torn_down:
                    _fire_state(
                        self.params.on_state_change, 'failed',
                        'JWT refresh failed: no OAuth token',
                    )
                return
            fresh = await _with_retry(
                lambda: fetch_remote_credentials(
                    self.session_id,
                    self.params.base_url,
                    oauth_token,
                    timeout_seconds=self.cfg.http_timeout_ms / 1000.0,
                    client=self.http_client,
                ),
                'fetchRemoteCredentials (recovery)',
                self.cfg,
            )
            if fresh is None or self.torn_down:
                if not self.torn_down:
                    _fire_state(
                        self.params.on_state_change, 'failed',
                        'JWT refresh failed after 401',
                    )
                return
            # If 401 interrupted the initial flush, writeBatch may have
            # silently no-op'd on the closed uploader. Reset so the new
            # onConnect re-flushes.
            self.initial_flush_done = False
            await self._rebuild_transport(fresh, 'auth_401_recovery')
            logger.debug('[remote-bridge] Transport rebuilt after 401')
        except Exception as err:  # noqa: BLE001  log + surface
            logger.error(
                '[remote-bridge] 401 recovery failed: %s', err
            )
            if not self.torn_down:
                _fire_state(
                    self.params.on_state_change, 'failed',
                    f'JWT refresh failed: {err}',
                )
        finally:
            self.auth_recovery_in_flight = False

    def on_jwt_refresh(self, session_id: str, oauth_token: str) -> None:
        """Called by ``TokenRefreshScheduler`` 5min before JWT expiry.

        Re-fetches ``/bridge`` and rebuilds the transport. Serialized
        against ``recover_from_auth_failure`` via ``auth_recovery_in_flight``
        so a laptop-wake double-fire doesn't bump epoch twice.
        """
        async def _do() -> None:
            if self.auth_recovery_in_flight or self.torn_down:
                logger.debug(
                    '[remote-bridge] Recovery already in flight, '
                    'skipping proactive refresh'
                )
                return
            self.auth_recovery_in_flight = True
            try:
                fresh = await _with_retry(
                    lambda: fetch_remote_credentials(
                        session_id,
                        self.params.base_url,
                        oauth_token,
                        timeout_seconds=self.cfg.http_timeout_ms / 1000.0,
                        client=self.http_client,
                    ),
                    'fetchRemoteCredentials (proactive)',
                    self.cfg,
                )
                if fresh is None or self.torn_down:
                    return
                await self._rebuild_transport(fresh, 'proactive_refresh')
                logger.debug(
                    '[remote-bridge] Transport rebuilt (proactive refresh)'
                )
            except Exception as err:  # noqa: BLE001
                logger.error(
                    '[remote-bridge] Proactive refresh rebuild failed: %s',
                    err,
                )
                if not self.torn_down:
                    _fire_state(
                        self.params.on_state_change, 'failed',
                        f'Refresh failed: {err}',
                    )
            finally:
                self.auth_recovery_in_flight = False

        asyncio.create_task(_do())

    # ── History flush + drain ──────────────────────────────────────────

    async def _flush_history(self, msgs: list[Message]) -> None:
        """POST a capped, eligible-only slice of initial history.

        Cap via ``initial_history_cap``; filter via
        ``is_eligible_bridge_message``. The cap takes the *tail* to keep
        the most recent context. If the eligible tail is a user message
        (pre-cap), reports state ``'running'`` so the web UI shows the
        turn-in-progress indicator immediately on connect.
        """
        eligible = [m for m in msgs if is_eligible_bridge_message(_msg_dict(m))]
        cap = self.params.initial_history_cap
        capped = (
            eligible[-cap:] if cap > 0 and len(eligible) > cap else eligible
        )
        if len(capped) < len(eligible):
            logger.debug(
                '[remote-bridge] Capped initial flush: %s -> %s (cap=%s)',
                len(eligible), len(capped), cap,
            )
        events = [
            {**m, 'session_id': self.session_id}
            for m in to_sdk_messages(capped)
        ]
        if not events:
            return
        # Pre-cap eligible tail decides reportState: the cap may truncate
        # to a user message even when the actual trailing message is
        # assistant. Match TS behavior exactly.
        if eligible and _msg_dict(eligible[-1]).get('type') == 'user':
            self.transport.report_state({'state': 'running'})
        logger.debug(
            '[remote-bridge] Flushing %s history events', len(events)
        )
        await self.transport.write_batch(events)

    def _drain_flush_gate(self) -> None:
        """Send any messages queued during the flush window."""
        msgs = self.flush_gate.end()
        if not msgs:
            return
        for msg in msgs:
            self.recent_posted_uuids.add(msg.uuid)
        events = [
            {**m, 'session_id': self.session_id}
            for m in to_sdk_messages(msgs)
        ]
        if any(_msg_dict(m).get('type') == 'user' for m in msgs):
            self.transport.report_state({'state': 'running'})
        logger.debug(
            '[remote-bridge] Drained %s queued message(s) after flush',
            len(msgs),
        )
        asyncio.create_task(self.transport.write_batch(events))

    # ── Public handle methods ──────────────────────────────────────────

    def write_messages(self, messages: list[Message]) -> None:
        """Write a batch of local Message[] to the transport."""
        filtered = [
            m for m in messages
            if is_eligible_bridge_message(_msg_dict(m))
            and m.uuid not in self.initial_message_uuids
            and not self.recent_posted_uuids.has(m.uuid)
        ]
        if not filtered:
            return

        # Title derivation — scan BEFORE the flushGate check so prompts
        # queued during flush still count.
        if not self.user_message_callback_done:
            for m in filtered:
                text = extract_title_text(_msg_dict(m))
                cb = self.params.on_user_message
                if text is not None and cb is not None and cb(text, self.session_id):
                    self.user_message_callback_done = True
                    break

        if self.flush_gate.enqueue(*filtered):
            logger.debug(
                '[remote-bridge] Queued %s message(s) during flush',
                len(filtered),
            )
            return

        for msg in filtered:
            self.recent_posted_uuids.add(msg.uuid)
        events = [
            {**m, 'session_id': self.session_id}
            for m in to_sdk_messages(filtered)
        ]
        if any(_msg_dict(m).get('type') == 'user' for m in filtered):
            self.transport.report_state({'state': 'running'})
        logger.debug(
            '[remote-bridge] Sending %s message(s)', len(filtered)
        )
        asyncio.create_task(self.transport.write_batch(events))

    def write_sdk_messages(self, messages: list[dict[str, Any]]) -> None:
        """Write pre-shaped SDK messages (used by the daemon path)."""
        filtered = [
            m for m in messages
            if not m.get('uuid') or not self.recent_posted_uuids.has(m['uuid'])
        ]
        if not filtered:
            return
        for msg in filtered:
            uid = msg.get('uuid')
            if uid:
                self.recent_posted_uuids.add(uid)
        events = [{**m, 'session_id': self.session_id} for m in filtered]
        asyncio.create_task(self.transport.write_batch(events))

    def send_control_request(self, request: dict[str, Any]) -> None:
        if self.auth_recovery_in_flight:
            logger.debug(
                '[remote-bridge] Dropping control_request during '
                '401 recovery: %s', request.get('request_id'),
            )
            return
        event = {**request, 'session_id': self.session_id}
        inner = request.get('request') or {}
        if inner.get('subtype') == 'can_use_tool':
            self.transport.report_state({'state': 'requires_action'})
        asyncio.create_task(self.transport.write(event))
        logger.debug(
            '[remote-bridge] Sent control_request request_id=%s',
            request.get('request_id'),
        )

    def send_control_response(self, response: dict[str, Any]) -> None:
        if self.auth_recovery_in_flight:
            logger.debug(
                '[remote-bridge] Dropping control_response during 401 recovery'
            )
            return
        event = {**response, 'session_id': self.session_id}
        self.transport.report_state({'state': 'running'})
        asyncio.create_task(self.transport.write(event))
        logger.debug('[remote-bridge] Sent control_response')

    def send_cancel_request(self, request_id: str) -> None:
        if self.auth_recovery_in_flight:
            logger.debug(
                '[remote-bridge] Dropping control_cancel_request '
                'during 401 recovery: %s', request_id,
            )
            return
        event = {
            'type': 'control_cancel_request',
            'request_id': request_id,
            'session_id': self.session_id,
        }
        # Hook/classifier/channel/recheck resolved the permission locally
        # — interactiveHandler calls only cancelRequest (no sendResponse)
        # on those paths, so without this the server stays on
        # requires_action.
        self.transport.report_state({'state': 'running'})
        asyncio.create_task(self.transport.write(event))
        logger.debug(
            '[remote-bridge] Sent control_cancel_request request_id=%s',
            request_id,
        )

    def send_result(self) -> None:
        if self.auth_recovery_in_flight:
            logger.debug(
                '[remote-bridge] Dropping result during 401 recovery'
            )
            return
        self.transport.report_state({'state': 'idle'})
        asyncio.create_task(
            self.transport.write(make_result_message(self.session_id))
        )
        logger.debug('[remote-bridge] Sent result')

    # ── Teardown ───────────────────────────────────────────────────────

    async def teardown(self) -> None:
        """Idempotent shutdown.

        1. Cancel JWT refresh scheduler.
        2. Drop the flush gate (any queued messages are lost — transport
           is about to close).
        3. ``reportState('idle')`` + write result message (fire-and-forget;
           archive latency covers the uploader drain).
        4. Archive the session via the v2 compat archive endpoint, with a
           single 401 retry through ``on_auth_401``.
        5. Close the transport.
        """
        if self.torn_down:
            return
        self.torn_down = True
        if self.refresh is not None:
            self.refresh.cancel_all()
        self.flush_gate.drop()

        # Fire the result message before archive. ``transport.write()``
        # in Python awaits enqueue into ``CCRClient.SerialBatchEventUploader``
        # (fast on the happy path) but blocks up to ``producer_timeout_seconds``
        # (default 30s) when the queue is full. ``gracefulShutdown`` races
        # this against a 2s cap, so bound the wait tightly — losing the
        # result message under back-pressure is preferable to blocking
        # teardown past the cap.
        self.transport.report_state({'state': 'idle'})
        try:
            await asyncio.wait_for(
                self.transport.write(make_result_message(self.session_id)),
                timeout=_TEARDOWN_RESULT_WRITE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                '[remote-bridge] Result write timed out after %ss '
                '(producer queue back-pressure)',
                _TEARDOWN_RESULT_WRITE_TIMEOUT_SECONDS,
            )
        except Exception as err:  # noqa: BLE001  log + carry on
            logger.warning('[remote-bridge] Result write failed: %s', err)

        token = self.params.get_access_token()
        archive_timeout = self.cfg.teardown_archive_timeout_ms / 1000.0
        status = await _archive_v2_session(
            self.session_id,
            self.params.base_url,
            token,
            self.params.org_uuid,
            archive_timeout,
            self.http_client,
        )

        # Single 401 retry — token might be stale post-laptop-wake.
        if status == 401 and self.params.on_auth_401 is not None:
            try:
                await self.params.on_auth_401(token or '')
                token = self.params.get_access_token()
                status = await _archive_v2_session(
                    self.session_id,
                    self.params.base_url,
                    token,
                    self.params.org_uuid,
                    archive_timeout,
                    self.http_client,
                )
            except Exception as err:  # noqa: BLE001
                logger.error(
                    '[remote-bridge] Teardown 401 retry threw: %s', err
                )

        self.transport.close()
        logger.debug(
            '[remote-bridge] Torn down (archive=%s)', status
        )


# ── Helpers ───────────────────────────────────────────────────────────────


def _freeze_token(token: str) -> Callable[[], str | None]:
    """Capture the JWT in a closure so the transport reads it stably.

    Per-instance auth header source; bypasses
    ``CLAUDE_CODE_SESSION_ACCESS_TOKEN`` env var which mcp/client reads
    ungatedly. Frozen at construction — the transport is fully rebuilt
    on JWT refresh, so the closure is reissued each time.
    """
    return lambda: token


def _fire_state(
    cb: OnStateChange | None,
    state: BridgeState,
    detail: str | None = None,
) -> None:
    """Invoke ``on_state_change`` if wired, swallowing exceptions."""
    if cb is None:
        return
    try:
        if detail is None:
            cb(state)
        else:
            cb(state, detail)
    except Exception as err:  # noqa: BLE001
        logger.warning(
            '[remote-bridge] on_state_change callback raised: %s', err
        )


def _msg_dict(msg: Message | dict[str, Any]) -> dict[str, Any]:
    """Coerce a Message dataclass to the wire-format dict shape.

    The dict-shaped predicates in ``bridge.messaging`` mirror the TS
    wire format where user/assistant messages have a nested
    ``message: {role, content}`` field. Python ``UserMessage`` /
    ``AssistantMessage`` keep ``role`` and ``content`` flat at the top
    level, so a shallow ``vars()`` would give ``extract_title_text``
    nothing to extract. We synthesize the nested ``message`` field
    on top of the flat ``vars()`` so both ``is_eligible_bridge_message``
    (reads ``type``/``isVirtual``/``subtype``) AND ``extract_title_text``
    (reads ``message.content``) work against the same dict.
    """
    if isinstance(msg, dict):
        return msg
    flat = vars(msg).copy()
    msg_type = flat.get('type')
    # Only user/assistant carry inner ``message.content`` in the wire
    # format; system and others stay flat.
    if msg_type in ('user', 'assistant') and 'message' not in flat:
        flat['message'] = {
            'role': flat.get('role', msg_type),
            'content': flat.get('content'),
        }
    return flat


async def _with_retry(
    fn: Callable[[], Awaitable[Any]],
    label: str,
    cfg: EnvLessBridgeConfig,
) -> Any:
    """Retry an async init call with exponential backoff + jitter.

    Mirrors TS ``withRetry`` on ``remoteBridgeCore.ts:891-913``. Returns
    the first non-``None`` result; ``None`` after all attempts indicates
    permanent failure (caller logs + degrades).
    """
    max_attempts = cfg.init_retry_max_attempts
    for attempt in range(1, max_attempts + 1):
        result = await fn()
        if result is not None:
            return result
        if attempt < max_attempts:
            base_ms = cfg.init_retry_base_delay_ms * (2 ** (attempt - 1))
            jitter_ms = (
                base_ms
                * cfg.init_retry_jitter_fraction
                * (2 * random.random() - 1)
            )
            delay_ms = min(base_ms + jitter_ms, cfg.init_retry_max_delay_ms)
            logger.debug(
                '[remote-bridge] %s failed (attempt %s/%s), '
                'retrying in %sms',
                label, attempt, max_attempts, round(delay_ms),
            )
            await asyncio.sleep(delay_ms / 1000.0)
    return None


def _async_refresh_token(
    params: EnvLessBridgeParams,
) -> Callable[[], Awaitable[str | None]]:
    """Build the async token getter for the JWT refresh scheduler.

    Always attempts OAuth refresh first — ``get_access_token()`` returns
    expired tokens as non-None strings (no expiresAt check), so we can't
    rely on truthiness alone. Passes the stale token to ``on_auth_401``
    so keychain-comparison can detect parallel refresh (Phase 10 wiring).
    """
    async def _refresh() -> str | None:
        stale = params.get_access_token()
        if params.on_auth_401 is not None:
            await params.on_auth_401(stale or '')
        return params.get_access_token() or stale

    return _refresh


async def _safe_archive_session(
    session_id: str,
    base_url: str,
    access_token: str | None,
    org_uuid: str,
    timeout_seconds: float,
    http_client: httpx.AsyncClient | None,
) -> None:
    """Best-effort archive used by the pre-flight failure paths.

    Wraps ``_archive_v2_session`` and swallows all exceptions — the
    caller is already about to return ``None`` from init, so any archive
    failure shouldn't prevent that.
    """
    try:
        await _archive_v2_session(
            session_id, base_url, access_token, org_uuid,
            timeout_seconds, http_client,
        )
    except Exception as err:  # noqa: BLE001
        logger.debug(
            '[remote-bridge] Pre-flight archive failed: %s', err
        )


_ARCHIVE_BETA_HEADER = 'ccr-byoc-2025-07-29'
_ANTHROPIC_VERSION = '2023-06-01'


async def _archive_v2_session(
    session_id: str,
    base_url: str,
    access_token: str | None,
    org_uuid: str,
    timeout_seconds: float,
    http_client: httpx.AsyncClient | None,
) -> int | str:
    """Archive a v2 session via the compat ``/v1/sessions/{id}/archive`` endpoint.

    Mirrors TS ``archiveSession`` on ``remoteBridgeCore.ts:963-1008``.
    The v2 archive lives at the compat layer (``/v1/sessions/*``, not
    ``/v1/code/sessions/*``) and requires distinct headers vs.
    ``bridge_api.archive_session`` (which targets the environments API):

    * ``anthropic-beta: ccr-byoc-2025-07-29``  (compat byoc)
    * ``x-organization-uuid``                  (required by compat gateway)

    Returns the HTTP status code on success, or ``'no_token'`` /
    ``'timeout'`` / ``'error'`` on failure. Idempotent — 409 (already
    archived) is success.
    """
    if not access_token:
        return 'no_token'
    compat_id = to_compat_session_id(session_id)
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'anthropic-version': _ANTHROPIC_VERSION,
        'anthropic-beta': _ARCHIVE_BETA_HEADER,
        'x-organization-uuid': org_uuid,
    }
    url = f'{base_url.rstrip("/")}/v1/sessions/{compat_id}/archive'
    try:
        if http_client is not None:
            response = await http_client.post(
                url, headers=headers, json={}, timeout=timeout_seconds,
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, headers=headers, json={}, timeout=timeout_seconds,
                )
    except httpx.TimeoutException:
        logger.debug('[remote-bridge] Archive %s timed out', compat_id)
        return 'timeout'
    except httpx.HTTPError as err:
        logger.debug('[remote-bridge] Archive %s failed: %s', compat_id, err)
        return 'error'
    logger.debug(
        '[remote-bridge] Archive %s status=%s', compat_id, response.status_code
    )
    return response.status_code


__all__ = [
    'BridgeState',
    'DEFAULT_ENV_LESS_BRIDGE_CONFIG',
    'EnvLessBridgeParams',
    'RemoteBridgeHandle',
    'init_env_less_bridge_core',
]

"""Server-initiated control_request handler.

Ports ``handle_server_control_request`` from
``typescript/src/bridge/bridgeMessaging.ts:243-391`` plus the per-subtype
dispatch (``initialize``, ``set_model``, ``set_max_thinking_tokens``,
``set_permission_mode``, ``interrupt``) AND the **unknown-subtype error
response default** + outbound-only error response.

Lives in a separate file from ``messaging.py`` so the router stays small
and stable while the handler set evolves.

Dispatch flow:
    server WS → handle_ingress_message (router)
              → on_control_request callback
              → handle_server_control_request (this module)
              → per-subtype handler (callbacks supplied by ``handlers``)
              → transport.write(control_response)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, Union

logger = logging.getLogger(__name__)

OUTBOUND_ONLY_ERROR = (
    'This session is outbound-only. Enable Remote Control locally to allow '
    'inbound control.'
)


# ─── Verdict type for set_permission_mode (WI-3.7a wiring point) ───────────


@dataclass(frozen=True)
class Ok:
    """Verdict: policy permitted the mode change."""


@dataclass(frozen=True)
class Err:
    """Verdict: policy rejected the mode change with a user-visible reason."""

    error: str


Verdict = Union[Ok, Err]


# ─── Transport surface this module needs ───────────────────────────────────


class _TransportLike(Protocol):
    """Minimal write-side surface used by ``handle_server_control_request``.

    Real implementations (``ReplBridgeTransport``,
    ``DirectConnectSessionManager``) provide more; this Protocol pins
    what we depend on.

    **Sync OR async write is supported.** TS ``ReplBridgeTransport.write``
    is async (returns ``Promise<void>`` per
    ``replBridgeTransport.ts:24``); TS consumers fire-and-forget with
    ``void transport.write(event)``. The Python equivalent: if ``write``
    returns an awaitable, ``_send`` schedules it on the running loop via
    ``asyncio.create_task``. If it returns ``None`` (sync), nothing extra
    happens. Callers MUST invoke ``handle_server_control_request`` from
    inside a running loop when the transport's ``write`` is async.
    """

    def write(  # pragma: no cover -- structural
        self, message: dict[str, Any]
    ) -> Any:
        ...


# ─── Handlers struct ───────────────────────────────────────────────────────


@dataclass
class ServerControlRequestHandlers:
    """Per-callsite wiring for the 5 control_request subtypes.

    Mirrors the TS ``ServerControlRequestHandlers`` type at
    ``bridgeMessaging.ts:212-229``.

    Required fields:
      transport — the write surface the response is sent through.
      session_id — included in every emitted control_response.

    Optional callbacks (None means "subtype not supported here"):
      on_interrupt — called for ``interrupt``; success response is sent.
      on_set_model — called for ``set_model(model)``; success response.
      on_set_max_thinking_tokens — called for that subtype; success.
      on_set_permission_mode — called for ``set_permission_mode(mode)``;
            **must return** ``Ok()`` or ``Err(error=...)``. If None, the
            handler returns ``Err`` with a "not supported in this context"
            message — the chapter-prescribed unknown-handler pattern at
            ``bridgeMessaging.ts:336-340``. WI-3.7b will replace the default
            with real policy gates once those land.

    outbound_only — when True, all mutable subtypes (everything except
    ``initialize``) reply with an error so claude.ai sees a proper error
    instead of false-success. ``initialize`` still succeeds — the server
    kills the connection within 10-14 s otherwise.
    """

    transport: _TransportLike
    session_id: str
    outbound_only: bool = False
    on_interrupt: Callable[[], None] | None = None
    on_set_model: Callable[[str | None], None] | None = None
    on_set_max_thinking_tokens: Callable[[int | None], None] | None = None
    on_set_permission_mode: Callable[[str], Verdict] | None = None


# ─── Main dispatch ─────────────────────────────────────────────────────────


def handle_server_control_request(
    request: dict[str, Any],
    handlers: ServerControlRequestHandlers,
) -> None:
    """Dispatch a server-initiated ``control_request`` message.

    Mirrors ``bridgeMessaging.ts:243-391``. Sends exactly one
    ``control_response`` via ``handlers.transport.write`` per call. Never
    silent — unknown subtypes get an error response so the server doesn't
    hang waiting (~10-14 s timeout before WS kill).
    """
    inner = request.get('request') or {}
    if not isinstance(inner, dict):
        logger.debug('[bridge:messaging] control_request.request is not a dict; ignoring')
        return
    request_id = request.get('request_id', '')
    subtype = inner.get('subtype')

    response_inner: dict[str, Any]

    # Outbound-only: reply error for mutable requests so claude.ai doesn't
    # show false success. ``initialize`` must still succeed (server kills
    # the connection if it doesn't).
    if handlers.outbound_only and subtype != 'initialize':
        response_inner = {
            'subtype': 'error',
            'request_id': request_id,
            'error': OUTBOUND_ONLY_ERROR,
        }
        _send(handlers, response_inner, subtype, kind='outbound-only')
        return

    if subtype == 'initialize':
        # Minimal capabilities — the REPL handles commands/models/account
        # info itself.
        response_inner = {
            'subtype': 'success',
            'request_id': request_id,
            'response': {
                'commands': [],
                'output_style': 'normal',
                'available_output_styles': ['normal'],
                'models': [],
                'account': {},
                'pid': os.getpid(),
            },
        }
    elif subtype == 'set_model':
        if handlers.on_set_model is not None:
            handlers.on_set_model(inner.get('model'))
        response_inner = {'subtype': 'success', 'request_id': request_id}
    elif subtype == 'set_max_thinking_tokens':
        if handlers.on_set_max_thinking_tokens is not None:
            handlers.on_set_max_thinking_tokens(inner.get('max_thinking_tokens'))
        response_inner = {'subtype': 'success', 'request_id': request_id}
    elif subtype == 'set_permission_mode':
        # The callback returns a Verdict so we can send an error
        # control_response without importing policy gates here.
        # If no callback is registered (daemon context, which doesn't
        # wire this), return an error verdict rather than silent
        # false-success — the chapter-prescribed pattern at
        # bridgeMessaging.ts:336-340 (WI-3.7b).
        mode = inner.get('mode', '')
        if handlers.on_set_permission_mode is None:
            verdict: Verdict = Err(
                error=(
                    'set_permission_mode is not supported in this context '
                    '(on_set_permission_mode callback not registered)'
                )
            )
        else:
            verdict = handlers.on_set_permission_mode(mode)
        if isinstance(verdict, Ok):
            response_inner = {'subtype': 'success', 'request_id': request_id}
        else:
            response_inner = {
                'subtype': 'error',
                'request_id': request_id,
                'error': verdict.error,
            }
    elif subtype == 'interrupt':
        if handlers.on_interrupt is not None:
            handlers.on_interrupt()
        response_inner = {'subtype': 'success', 'request_id': request_id}
    else:
        # Unknown subtype — respond with error so the server doesn't
        # hang waiting for a reply that never comes. Chapter explicit
        # pattern at bridgeMessaging.ts:373-384.
        response_inner = {
            'subtype': 'error',
            'request_id': request_id,
            'error': f'REPL bridge does not handle control_request subtype: {subtype}',
        }

    _send(handlers, response_inner, subtype, kind='dispatch')


def _send(
    handlers: ServerControlRequestHandlers,
    response_inner: dict[str, Any],
    subtype: object,
    *,
    kind: str,
) -> None:
    """Build the ``control_response`` envelope and write it.

    Supports both sync and async ``transport.write``. If ``write``
    returns an awaitable (the TS-equivalent path), schedule it on the
    running loop via ``create_task`` — matches TS's
    ``void transport.write(event)`` fire-and-forget at
    ``bridgeMessaging.ts:278, 387``.
    """
    envelope: dict[str, Any] = {
        'type': 'control_response',
        'response': response_inner,
        'session_id': handlers.session_id,
    }
    result = handlers.transport.write(envelope)
    if inspect.isawaitable(result):
        asyncio.get_running_loop().create_task(result)
    logger.debug(
        '[bridge:messaging] Sent control_response (%s) for %s request_id=%s result=%s',
        kind,
        subtype,
        response_inner.get('request_id'),
        response_inner.get('subtype'),
    )


__all__ = [
    'Err',
    'OUTBOUND_ONLY_ERROR',
    'Ok',
    'ServerControlRequestHandlers',
    'Verdict',
    'handle_server_control_request',
]

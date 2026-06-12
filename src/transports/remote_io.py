"""Async StructuredIO-style bridge over a Transport.

Port of ``typescript/src/cli/remoteIO.ts`` (structural skeleton).

Lives in ``src/transports/`` (not ``src/cli_core/``) because Python's
``cli_core`` is pure sync; ``RemoteIO`` is async (owns an async
Transport, async input stream). See ``my-docs/get-parity-by-folder/
cli-gap-analysis.md`` §2.5.

Scope of this port
------------------

This is the **structural skeleton + bridge-mode keep-alive** only.

In scope:

* Constructor wires a Transport (via :func:`get_transport_for_url`),
  binds ``set_on_data`` / ``set_on_close`` to an async input queue,
  fires connect.
* ``write(message)`` forwards to ``transport.write`` for WS/Hybrid
  transports.
* Bridge-mode (``CLAUDE_CODE_ENVIRONMENT_KIND == "bridge"``) stdout
  echo of ``control_request`` messages.
* Bridge-mode keep-alive task driven by
  ``get_poll_interval_config().session_keepalive_interval_v2_ms``.
* Initial-prompt consumption.

Out of scope (TODOs, pinned by cli-gap-analysis.md §3.3 / §4.7)
---------------------------------------------------------------

* **CCR v2 write path.** ``SSETransport`` has no ``write`` method — the
  TS code routes writes through ``CCRClient.writeEvent`` for that case
  (TS ``remoteIO.ts:232-236``). Wiring the Python ``CCRClient`` (which
  has a different constructor shape: ``(base_url, options,
  http_client=…)`` + separate ``initialize(epoch)``) into ``RemoteIO``
  requires deciding how the epoch is obtained — that belongs in the
  ``ccr_client.py`` deep-port audit. **For this PR**,
  ``RemoteIO.__init__`` raises ``NotImplementedError`` if the selected
  transport doesn't expose ``write`` (currently only ``SSETransport``).
* CCR v2 internal-event reader/writer registration
  (``setInternalEventWriter``, ``setInternalEventReader``).
* Session-state listeners (``setCommandLifecycleListener``,
  ``setSessionStateChangedListener``,
  ``setSessionMetadataChangedListener``).

No-consumer note
----------------

No Python module instantiates ``RemoteIO`` today. The bridge runner has
its own transport wiring in ``src/bridge/repl_bridge_transport.py``.
This port satisfies inventory parity for
``typescript/src/cli/remoteIO.ts``; tests cover the class via direct
construction with monkeypatched transport stubs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from src.bridge.poll_config import get_poll_interval_config
from src.cli_core.ndjson import ndjson_safe_dumps
from src.transports.transport_utils import (
    Transport,
    get_transport_for_url,
    is_env_truthy,
)
from src.utils.session_ingress_auth import (
    get_session_ingress_auth_headers,
    get_session_ingress_auth_token,
)

logger = logging.getLogger(__name__)


def _is_debug_mode() -> bool:
    """Port of TS ``isDebugMode()`` (utils/debug.ts:44-55).

    TS checks ``DEBUG`` and ``DEBUG_SDK`` env vars (plus the ``--debug``
    argv flag which is REPL-runtime-only). The argv check is not
    relevant for RemoteIO; we mirror only the env-var portion.
    """
    return is_env_truthy(os.environ.get("DEBUG")) or is_env_truthy(
        os.environ.get("DEBUG_SDK")
    )


# Sentinel object signaling end-of-stream on the input queue.
_END_OF_STREAM: object = object()


class RemoteIO:
    """StructuredIO-style stdin/stdout bridge over a Transport.

    Constructor MUST be called inside an asyncio running loop — it uses
    ``asyncio.get_running_loop()`` to schedule the connect / keep-alive
    / initial-prompt tasks.
    """

    def __init__(
        self,
        stream_url: str,
        *,
        initial_prompt: AsyncIterable[str] | None = None,
        replay_user_messages: bool = False,
    ) -> None:
        self._url_str = stream_url
        self._replay_user_messages = replay_user_messages
        self._closed = False
        # asyncio.Queue safe within the same event loop. _on_data is
        # called from the transport's reader task on the same loop, so
        # put_nowait is the correct primitive (no cross-thread concerns).
        self._input_queue: asyncio.Queue[str | object] = asyncio.Queue()
        self._input_iter: AsyncIterator[str] | None = None
        self._keep_alive_task: asyncio.Task[None] | None = None
        self._initial_prompt_task: asyncio.Task[None] | None = None
        self._connect_task: asyncio.Task[None] | None = None

        self._is_bridge = (
            os.environ.get("CLAUDE_CODE_ENVIRONMENT_KIND") == "bridge"
        )
        self._is_debug = _is_debug_mode()

        # Header building uses the canonical helpers. These handle BOTH
        # bearer-auth JWTs AND session-key cookie auth for sk-ant-sid-*
        # tokens. A hand-rolled "Authorization: Bearer <token>" header
        # would silently break session-key tokens.
        def build_headers() -> dict[str, str]:
            h = dict(get_session_ingress_auth_headers())
            er_version = os.environ.get("CLAUDE_CODE_ENVIRONMENT_RUNNER_VERSION")
            if er_version:
                h["x-environment-runner-version"] = er_version
            return h

        if get_session_ingress_auth_token() is None:
            logger.error("[remote-io] No session ingress token available")

        # TS getSessionId() parity (#284): the bootstrap session ID
        # identifies this peer to the transport layer. Fail-soft — a
        # broken bootstrap import must not block the transport.
        try:
            from src.bootstrap.state import get_session_id

            session_id: str | None = get_session_id()
        except Exception:
            session_id = None

        self._transport: Transport = get_transport_for_url(
            self._url_str,
            headers=build_headers(),
            session_id=session_id,
            refresh_headers=build_headers,
        )

        # CCR v2 write path is out of scope for this PR — see module
        # docstring "Out of scope". Detect via the duck test: every
        # WS/Hybrid transport has `write`; SSETransport does not.
        # (TS uses a stricter `instanceof SSETransport` check at
        # remoteIO.ts:121-125 in the inverse direction. Here a non-SSE
        # transport that happens to lack `write` would also be rejected,
        # which is fine — all real transports either have it or are
        # known to be SSE.)
        if not hasattr(self._transport, "write"):
            raise NotImplementedError(
                "RemoteIO does not support CCR v2 (SSETransport write path) "
                "in this PR. The TS code routes CCR v2 writes through "
                "CCRClient.writeEvent (remoteIO.ts:232-236); the Python "
                "CCRClient has a different constructor that needs an epoch "
                "from somewhere — wiring belongs in the ccr_client.py "
                "deep-port audit. See cli-gap-analysis.md §3.3 / §4.7."
            )

        # on_data → input queue
        self._transport.set_on_data(self._on_data)
        # on_close → end-of-stream sentinel; signature takes the close
        # code per WebSocketTransport.set_on_close (line 263) and
        # SSETransport.set_on_close (line 109).
        self._transport.set_on_close(self._on_close)

        # Fire connect (no await; runs on the same loop).
        loop = asyncio.get_running_loop()
        self._connect_task = loop.create_task(self._transport.connect())

        # Bridge-mode keep-alive timer.
        if self._is_bridge:
            interval_ms = get_poll_interval_config().session_keepalive_interval_v2_ms
            if interval_ms > 0:
                self._keep_alive_task = loop.create_task(
                    self._keep_alive_loop(interval_ms / 1000)
                )

        # Initial prompt drain.
        if initial_prompt is not None:
            self._initial_prompt_task = loop.create_task(
                self._consume_initial_prompt(initial_prompt)
            )

    # -- Input side ----------------------------------------------------------

    def _on_data(self, data: str) -> None:
        # Bridge + debug: echo to stdout.
        if self._is_bridge and self._is_debug:
            sys.stdout.write(data if data.endswith("\n") else data + "\n")
            sys.stdout.flush()
        self._input_queue.put_nowait(data)

    def _on_close(self, code: int | None = None) -> None:
        # Signature matches WebSocketTransport.set_on_close (line 263)
        # and SSETransport.set_on_close (line 109). The close code is
        # accepted but currently unused — the sentinel is what drives
        # iterator termination.
        del code  # unused
        self._input_queue.put_nowait(_END_OF_STREAM)

    @property
    def input_stream(self) -> AsyncIterator[str]:
        """Async iterator over inbound transport data.

        Cached: every access returns the same generator so two callers
        of ``io.input_stream`` don't fight over the queue (TS exposes a
        singleton ``PassThrough``; we match the singleton semantics).
        """
        if self._input_iter is None:
            self._input_iter = self._iter_input()
        return self._input_iter

    async def _iter_input(self) -> AsyncIterator[str]:
        while True:
            item = await self._input_queue.get()
            if item is _END_OF_STREAM:
                return
            # Type-safe alternative to `assert isinstance(item, str)`
            # which is stripped under -O. The queue only ever holds
            # strings or the sentinel — narrow explicitly.
            if not isinstance(item, str):
                raise TypeError(
                    f"RemoteIO input queue produced non-str non-sentinel: "
                    f"{type(item).__name__}"
                )
            yield item

    async def _consume_initial_prompt(self, prompt: AsyncIterable[str]) -> None:
        async for chunk in prompt:
            # Strip a SINGLE trailing newline, append a fresh one —
            # matches TS `.replace(/\n$/, '')` exactly. Python's
            # `rstrip("\n")` would strip MULTIPLE trailing newlines,
            # changing the semantics for chunks like "abc\n\n" (TS
            # preserves the second \n as a paragraph break).
            s = str(chunk)
            line = (s[:-1] if s.endswith("\n") else s) + "\n"
            self._input_queue.put_nowait(line)

    # -- Output side ---------------------------------------------------------

    async def write(self, message: dict[str, Any]) -> None:
        """Send a message over the transport.

        In bridge mode, control_request messages are always echoed to
        stdout (so the bridge parent can detect permission requests).
        Other message types are echoed only in debug.
        """
        # Note: `write` was verified-present on this transport at
        # construction time (the NotImplementedError gate above).
        await self._transport.write(message)  # type: ignore[attr-defined]
        if self._is_bridge:
            if message.get("type") == "control_request" or self._is_debug:
                sys.stdout.write(ndjson_safe_dumps(message) + "\n")
                sys.stdout.flush()

    # -- Keep-alive ----------------------------------------------------------

    async def _keep_alive_loop(self, interval_s: float) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(interval_s)
                if self._closed:
                    return
                try:
                    await self.write({"type": "keep_alive"})
                except Exception as exc:  # noqa: BLE001
                    logger.debug("RemoteIO keep_alive write failed: %s", exc)
        except asyncio.CancelledError:
            pass

    # -- Internal events (overridden by future CCR v2 wiring) -----------------

    async def flush_internal_events(self) -> None:
        """No-op default. Override when CCR v2 internal events land."""
        return None

    @property
    def internal_events_pending(self) -> int:
        """Always 0 in the base class — override when CCR v2 lands."""
        return 0

    # -- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._keep_alive_task is not None:
            self._keep_alive_task.cancel()
            self._keep_alive_task = None
        if self._initial_prompt_task is not None:
            self._initial_prompt_task.cancel()
            self._initial_prompt_task = None
        if self._connect_task is not None:
            # If close() fires before the connect coroutine has run, the
            # transport.close() below would race with a never-started
            # connect — cancel it explicitly.
            self._connect_task.cancel()
            self._connect_task = None
        try:
            self._transport.close()
        except Exception:  # noqa: BLE001
            pass
        # Wake any iterator that's blocked on the queue.
        self._input_queue.put_nowait(_END_OF_STREAM)

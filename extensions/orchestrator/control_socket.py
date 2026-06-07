"""F-49 Phase 1: Unix domain socket for live control of an agent run.

Path: ``{workspace}/.run_control/{run_id}.sock``. The socket accepts
multiple concurrent clients. Incoming lines are newline-delimited JSON
``ControlCommand`` objects. Outgoing lines (via ``send_event``) are
``EventFrame`` JSON.

**Scope:**
  * ``ControlCommand.cmd`` ∈ {pause, resume, inject, stop, detach, takeover}
  * ``inject`` / ``detach`` are parsed but the agent side is a no-op for
    now (Phase 2 wires ``inject`` to ``operator_hints.md``; ``detach`` is
    a Phase 3 hook).
  * No auth: workspace filesystem permissions are the only gate.
  * No Windows support: Unix-domain-socket only. Per the FEATURE_PLAN
    §3.1.11 risk table (line 1306) Windows falls back to a TCP localhost
    socket in a later phase.
  * Long content (transcript, large tool outputs) does NOT flow over
    this socket — that lives in ``transcript.jsonl``. The socket carries
    small control + small event frames only (typical < 1 KB).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Literal

logger = logging.getLogger(__name__)

ControlCmd = Literal["pause", "resume", "inject", "stop", "detach", "takeover"]


@dataclass
class ControlCommand:
    """A control command received from a socket client.

    ``cmd`` is the verb (pause / resume / inject / stop / detach /
    takeover). ``payload`` is an opaque string whose meaning depends on
    the verb: for ``resume`` it overrides the agent's next prompt, for
    ``inject`` it is a free-form hint, for the others it is ignored.
    """

    cmd: ControlCmd
    payload: str = ""


@dataclass
class EventFrame:
    """An event broadcast to all connected clients.

    ``type`` is the event class name (TextDelta / ToolCallEvent /
    ToolResultEvent / TurnComplete / PhaseComplete / SessionComplete).
    ``data`` is the JSON-safe payload. ``ts`` is the wall-clock emit
    time (seconds since epoch).
    """

    type: str
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class ControlSocket:
    """Bidirectional control via Unix domain socket.

    Lifecycle: ``start()`` → ``poll_commands()`` / ``send_event()`` →
    ``stop()``. ``start()`` is idempotent: a stale socket file from a
    previous unclean shutdown is unlinked best-effort. ``stop()`` is
    idempotent: closing twice does not raise.

    Concurrency: ``start()`` must be called from the same event loop
    that will drive ``poll_commands()`` and ``send_event()``. The class
    holds no thread-local state; cross-loop reuse is undefined.

    Failure handling: every public method except ``start()`` swallows
    exceptions and logs them. A broken socket must never kill the
    agent — the agent runner wraps each call site in its own
    try/except as well.
    """

    def __init__(self, sock_path: Path) -> None:
        self._path = Path(sock_path)
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        # Per-connection read-loop tasks. Tracked so ``stop()`` can
        # cancel them; otherwise they would leak and the process
        # would hang on shutdown.
        self._read_tasks: set[asyncio.Task[None]] = set()
        self._command_queue: asyncio.Queue[ControlCommand] = asyncio.Queue()
        self._stopped = False
        self._stale_unlinked = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start listening. Unlinks a stale socket file first.

        Raises ``OSError`` if the bind fails (e.g. permission denied on
        the parent directory). Callers should treat this as fatal for
        the socket but non-fatal for the agent — wrap in try/except
        and set ``session.control_socket = None`` on failure.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists() and not self._stale_unlinked:
            try:
                self._path.unlink()
                self._stale_unlinked = True
            except OSError as exc:
                logger.warning(
                    "control_socket: could not unlink stale socket %s: %s",
                    self._path, exc,
                )
        self._server = await asyncio.start_unix_server(
            self._on_client_connected, path=str(self._path),
        )

    async def stop(self) -> None:
        """Stop listening and remove the socket file. Idempotent."""
        self._stopped = True
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        # Cancel any per-connection read loops first so they don't
        # try to drain a closed transport.
        for t in list(self._read_tasks):
            if not t.done():
                t.cancel()
        for t in list(self._read_tasks):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._read_tasks.clear()
        for w in list(self._clients):
            try:
                w.close()
            except Exception:
                pass
        self._clients.clear()
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError as exc:
            logger.warning(
                "control_socket: could not unlink %s on stop: %s",
                self._path, exc,
            )

    # ------------------------------------------------------------------
    # Inbound: control commands
    # ------------------------------------------------------------------

    async def poll_commands(self) -> AsyncIterator[ControlCommand]:
        """Yield commands as they arrive.

        The iterator polls an internal ``asyncio.Queue`` with a 0.5s
        timeout; on timeout it loops back to the ``while not
        self._stopped`` check so ``stop()`` can terminate the
        iterator promptly. The 0.5s timeout is the upper bound on
        shutdown latency.
        """
        while not self._stopped:
            try:
                cmd = await asyncio.wait_for(
                    self._command_queue.get(), timeout=0.5,
                )
                yield cmd
            except asyncio.TimeoutError:
                continue

    # ------------------------------------------------------------------
    # Outbound: event broadcast
    # ------------------------------------------------------------------

    async def send_event(self, event: dict | EventFrame) -> None:
        """Broadcast an event to all connected clients as one JSON line.

        No-op if no clients are connected. Dead clients (write raises)
        are silently dropped from the client set; the remaining
        clients still receive the event.
        """
        if not self._clients:
            return
        if isinstance(event, EventFrame):
            frame = asdict(event)
        else:
            frame = dict(event)
            frame.setdefault("ts", time.time())
        line = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
        dead: list[asyncio.StreamWriter] = []
        for w in self._clients:
            try:
                w.write(line)
                await w.drain()
            except Exception:
                dead.append(w)
        for w in dead:
            self._clients.discard(w)
            try:
                w.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_client_connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        """Sync connection callback.

        We register the writer in ``_clients`` immediately (before the
        accept task yields) so a client that connects and immediately
        writes a command will see the writer registered. If we used a
        coroutine callback, the registration would happen after the
        first ``await`` in the read loop, racing with the client's
        first ``send_event`` call.
        """
        self._clients.add(writer)
        task = asyncio.create_task(self._read_loop(reader, writer))
        self._read_tasks.add(task)
        task.add_done_callback(self._read_tasks.discard)

    async def _read_loop(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        """Per-connection read loop: newline-delimited JSON commands."""
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.warning(
                        "control_socket: malformed command on %s: %s",
                        self._path, exc,
                    )
                    continue
                if not isinstance(payload, dict):
                    logger.warning(
                        "control_socket: ignoring non-dict command: %r",
                        payload,
                    )
                    continue
                try:
                    cmd = ControlCommand(
                        cmd=payload["cmd"],
                        payload=str(payload.get("payload", "")),
                    )
                except (KeyError, TypeError) as exc:
                    logger.warning(
                        "control_socket: invalid command shape: %s", exc,
                    )
                    continue
                await self._command_queue.put(cmd)
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass

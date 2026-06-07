"""F-49 Phase 1 tests for the Unix control socket.

Covers:
  * Lifecycle: start creates the socket file, stale files are unlinked,
    stop removes the file, both ops are idempotent.
  * Command receiving: pause / resume+payload, malformed JSON is
    skipped, invalid shapes are skipped, FIFO ordering holds.
  * Event broadcasting: single + multi-client, no-clients no-op,
    disconnected client doesn't break broadcast, ``EventFrame`` is
    serialized with ``ts``.
  * AgentSession integration: socket attaches to a real session, events
    dispatched via ``send_event`` reach connected clients, commands
    received via the socket drive session state via ``poll_commands``.

Uses ``unittest.IsolatedAsyncioTestCase`` (the repo's canonical async
test pattern) and ``tempfile.TemporaryDirectory`` for socket path
isolation.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import AsyncIterator

from extensions.orchestrator.control_socket import (
    ControlCommand,
    ControlSocket,
    EventFrame,
)


def _sock_path(tmp: Path, name: str = "ctrl.sock") -> Path:
    return tmp / name


async def _drain_one(cs: ControlSocket, timeout: float = 2.0) -> ControlCommand | None:
    """Pull one command from ``poll_commands``, or None on timeout."""
    async def _next() -> ControlCommand | None:
        async for cmd in cs.poll_commands():
            return cmd
        return None
    return await asyncio.wait_for(_next(), timeout=timeout)


async def _open_client(sock_path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    return reader, writer


async def _wait_for_clients(
    cs: ControlSocket, expected: int = 1, timeout: float = 2.0,
) -> None:
    """Poll until the server has registered ``expected`` clients.

    The Unix-socket accept path runs the client callback inside a
    separate task scheduled by the server, so a client that connects
    and immediately calls ``send_event`` races the accept task. The
    broadcast silently no-ops if ``_clients`` is still empty, so
    callers should await this helper before broadcasting.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(cs._clients) >= expected:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(
        f"Expected {expected} connected client(s), got {len(cs._clients)}",
    )


async def _send_cmd(
    writer: asyncio.StreamWriter, cmd: str, payload: str = "",
) -> None:
    writer.write(
        (json.dumps({"cmd": cmd, "payload": payload}) + "\n").encode("utf-8"),
    )
    await writer.drain()


async def _read_line(
    reader: asyncio.StreamReader, timeout: float = 2.0,
) -> str:
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return line.decode("utf-8").rstrip("\n")


class TestControlSocketLifecycle(unittest.IsolatedAsyncioTestCase):
    """start() / stop() behavior and file-system hygiene."""

    async def test_start_creates_socket_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            try:
                await cs.start()
                self.assertTrue(path.exists())
            finally:
                await cs.stop()

    async def test_start_unlinks_stale_socket(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            path.write_text("stale", encoding="utf-8")
            self.assertTrue(path.exists())
            cs = ControlSocket(path)
            try:
                await cs.start()
                # After start, the stale file should have been removed
                # and replaced with a listening socket. Path may or may
                # not exist depending on whether the bind re-creates it,
                # but a connection must succeed.
                reader, writer = await _open_client(path)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            finally:
                await cs.stop()

    async def test_stop_removes_socket_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            self.assertTrue(path.exists())
            await cs.stop()
            self.assertFalse(path.exists())

    async def test_idempotent_start_and_stop(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            await cs.start()  # second start is a no-op (server replaces)
            await cs.stop()
            await cs.stop()  # second stop must not raise


class TestControlSocketCommandReceiving(unittest.IsolatedAsyncioTestCase):
    """Inbound command parsing and FIFO delivery."""

    async def test_receive_pause_command(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                reader, writer = await _open_client(path)
                try:
                    await _send_cmd(writer, "pause")
                    cmd = await _drain_one(cs)
                    self.assertIsNotNone(cmd)
                    assert cmd is not None
                    self.assertEqual(cmd.cmd, "pause")
                    self.assertEqual(cmd.payload, "")
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            finally:
                await cs.stop()

    async def test_receive_resume_with_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                reader, writer = await _open_client(path)
                try:
                    await _send_cmd(writer, "resume", "new prompt text")
                    cmd = await _drain_one(cs)
                    assert cmd is not None
                    self.assertEqual(cmd.cmd, "resume")
                    self.assertEqual(cmd.payload, "new prompt text")
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            finally:
                await cs.stop()

    async def test_invalid_json_is_logged_and_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                reader, writer = await _open_client(path)
                try:
                    writer.write(b"not valid json\n")
                    await writer.drain()
                    await _send_cmd(writer, "stop")
                    cmd = await _drain_one(cs)
                    assert cmd is not None
                    # Only the valid command reaches the queue.
                    self.assertEqual(cmd.cmd, "stop")
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            finally:
                await cs.stop()

    async def test_command_without_cmd_key_is_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                reader, writer = await _open_client(path)
                try:
                    writer.write((json.dumps({"foo": "bar"}) + "\n").encode("utf-8"))
                    await writer.drain()
                    await _send_cmd(writer, "takeover")
                    cmd = await _drain_one(cs)
                    assert cmd is not None
                    self.assertEqual(cmd.cmd, "takeover")
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            finally:
                await cs.stop()

    async def test_multiple_commands_in_order(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                reader, writer = await _open_client(path)
                try:
                    await _send_cmd(writer, "pause")
                    await _send_cmd(writer, "resume", "p1")
                    await _send_cmd(writer, "stop")
                    seen: list[ControlCommand] = []
                    async for cmd in cs.poll_commands():
                        seen.append(cmd)
                        if len(seen) == 3:
                            break
                    self.assertEqual([c.cmd for c in seen], ["pause", "resume", "stop"])
                    self.assertEqual(seen[1].payload, "p1")
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            finally:
                await cs.stop()


class TestControlSocketEventBroadcasting(unittest.IsolatedAsyncioTestCase):
    """Outbound event fan-out to one or more connected clients."""

    async def test_broadcast_to_single_client(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                reader, writer = await _open_client(path)
                await _wait_for_clients(cs, expected=1)
                try:
                    await cs.send_event({
                        "type": "tool_call",
                        "data": {"tool_name": "Read", "params": {"path": "/x"}},
                    })
                    line = await _read_line(reader)
                    payload = json.loads(line)
                    self.assertEqual(payload["type"], "tool_call")
                    self.assertEqual(payload["data"]["tool_name"], "Read")
                    self.assertIn("ts", payload)
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            finally:
                await cs.stop()

    async def test_broadcast_to_multiple_clients(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                r1, w1 = await _open_client(path)
                r2, w2 = await _open_client(path)
                await _wait_for_clients(cs, expected=2)
                try:
                    await cs.send_event({"type": "text", "data": {"x": 1}})
                    line1 = await _read_line(r1)
                    line2 = await _read_line(r2)
                    self.assertEqual(json.loads(line1), json.loads(line2))
                finally:
                    w1.close()
                    w2.close()
                    for w in (w1, w2):
                        try:
                            await w.wait_closed()
                        except Exception:
                            pass
            finally:
                await cs.stop()

    async def test_no_clients_is_noop(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                # No clients connected — must not raise.
                await cs.send_event({"type": "x", "data": {}})
            finally:
                await cs.stop()

    async def test_disconnected_client_does_not_break_broadcast(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                r1, w1 = await _open_client(path)
                r2, w2 = await _open_client(path)
                await _wait_for_clients(cs, expected=2)
                # Close the first client abruptly.
                w1.close()
                try:
                    await w1.wait_closed()
                except Exception:
                    pass
                # Give the server a moment to notice the EOF and drop
                # the dead writer from ``_clients``.
                for _ in range(50):
                    if len(cs._clients) == 1:
                        break
                    await asyncio.sleep(0.01)
                await cs.send_event({"type": "alive", "data": {"v": 1}})
                # r2 should still receive the broadcast.
                line = await _read_line(r2)
                self.assertEqual(json.loads(line)["type"], "alive")
                w2.close()
                try:
                    await w2.wait_closed()
                except Exception:
                    pass
            finally:
                await cs.stop()

    async def test_eventframe_dataclass_serialized(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _sock_path(Path(tmp))
            cs = ControlSocket(path)
            await cs.start()
            try:
                reader, writer = await _open_client(path)
                await _wait_for_clients(cs, expected=1)
                try:
                    frame = EventFrame(
                        type="turn_complete",
                        data={"turn": 3},
                        ts=1234567890.5,
                    )
                    await cs.send_event(frame)
                    line = await _read_line(reader)
                    payload = json.loads(line)
                    self.assertEqual(payload["type"], "turn_complete")
                    self.assertEqual(payload["data"], {"turn": 3})
                    self.assertEqual(payload["ts"], 1234567890.5)
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            finally:
                await cs.stop()


class TestControlSocketAgentSessionIntegration(unittest.IsolatedAsyncioTestCase):
    """Verify the socket plays nicely with a real AgentSession.

    These tests don't run the full ``AgentRunner.run()`` (that's covered
    by the runner's own test suite). They construct an ``AgentSession``
    directly and exercise the socket's connect / broadcast / poll
    surfaces against it.
    """

    async def test_attach_socket_to_session_and_broadcast(self) -> None:
        from extensions.orchestrator.issue import Issue
        from extensions.orchestrator.agent_runner import AgentSession
        from extensions.orchestrator.workspace import Workspace

        with TemporaryDirectory() as tmp:
            ws_path = Path(tmp) / "ws"
            ws_path.mkdir()
            sock_path = ws_path / ".run_control" / "test.sock"
            cs = ControlSocket(sock_path)
            await cs.start()
            try:
                session = AgentSession(
                    issue=Issue(
                        id="I-1", identifier="ISSUE-1", title="t",
                    ),
                    workspace=Workspace(
                        path=str(ws_path),
                        issue_identifier="ISSUE-1",
                        issue_id="I-1",
                    ),
                    run_id="run-1",
                )
                session.control_socket = cs
                session.control_socket_path = str(sock_path)

                reader, writer = await _open_client(sock_path)
                await _wait_for_clients(cs, expected=1)
                try:
                    # Simulate the runner dispatching a ToolCallEvent.
                    await cs.send_event({
                        "type": "ToolCallEvent",
                        "data": {
                            "tool_name": "Read",
                            "tool_use_id": "A",
                            "params": {"path": "/x"},
                        },
                    })
                    line = await _read_line(reader)
                    payload = json.loads(line)
                    self.assertEqual(payload["type"], "ToolCallEvent")
                    self.assertEqual(payload["data"]["tool_name"], "Read")
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            finally:
                await cs.stop()

    async def test_pause_command_drives_session_state(self) -> None:
        import asyncio as _asyncio

        from extensions.orchestrator.issue import Issue
        from extensions.orchestrator.agent_runner import AgentSession
        from extensions.orchestrator.workspace import Workspace

        with TemporaryDirectory() as tmp:
            ws_path = Path(tmp) / "ws"
            ws_path.mkdir()
            sock_path = ws_path / ".run_control" / "test.sock"
            cs = ControlSocket(sock_path)
            await cs.start()
            try:
                session = AgentSession(
                    issue=Issue(
                        id="I-1", identifier="ISSUE-1", title="t",
                    ),
                    workspace=Workspace(
                        path=str(ws_path),
                        issue_identifier="ISSUE-1",
                        issue_id="I-1",
                    ),
                    run_id="run-1",
                )
                session.control_socket = cs
                session.pause_resume_event = _asyncio.Event()
                self.assertFalse(session.paused)

                reader, writer = await _open_client(sock_path)
                try:
                    await _send_cmd(writer, "pause")
                    # Mimic the runner's turn-boundary handler: drain one
                    # command and apply it.
                    async for cmd in cs.poll_commands():
                        if cmd.cmd == "pause":
                            session.paused = True
                            session.pause_resume_event.clear()
                        break
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass

                self.assertTrue(session.paused)
            finally:
                await cs.stop()


if __name__ == "__main__":
    unittest.main()

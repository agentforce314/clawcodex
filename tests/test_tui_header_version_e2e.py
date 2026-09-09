"""The header box shows the version the BACKEND reports.

End-to-end proof for the drift bug: the banner read ``clawcodex v1.4.0`` on a
v1.6.0 build because the Ink client filled ``SessionInfo.version`` from a
constant of its own instead of from the ``system/init`` frame. Same PTY + pyte
shape as tests/test_tui_recap_ghost_e2e.py — the real TUI bundle against a
scripted agent-server, screen read through a terminal emulator.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENTRY = REPO / "ui-tui" / "dist" / "entry.js"

pyte = pytest.importorskip("pyte", reason="pyte not installed (dev-only e2e)")
# importorskip, NOT a top-level import: `pty` does not exist on Windows and a
# module-level ImportError is a pytest COLLECTION ERROR there, so the win32
# skipif below would never get to apply.
pty = pytest.importorskip("pty", reason="POSIX pty required")

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty required"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH"),
    pytest.mark.skipif(not ENTRY.exists(), reason="ui-tui/dist not built"),
]

# A distinctive version no constant in the client could coincidentally hold —
# if the header shows this, it can only have come off the wire.
BACKEND_VERSION = "9.9.9"

# Minimal agent-server: one init frame, then an empty reply to every control
# request so the startup RPCs resolve and the composer paints. `%(version)s` is
# spliced in as a JSON fragment: a quoted string for the normal case, or the
# bare word `null`-free omission handled by the caller.
FAKE_SERVER = textwrap.dedent(
    """
    import json, sys

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    init = {
        "type": "system", "subtype": "init", "session_id": "s1",
        "model": "fake-model", "tools": [], "permission_mode": "default",
        "protocol_version": "0.1.0", "cwd": ".",
    }
    version = %(version)r
    if version is not None:
        init["version"] = version
    emit(init)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "control_request":
            req = msg.get("request") or {}
            emit({"type": "control_response", "response": {
                "subtype": req.get("subtype", ""),
                "request_id": msg.get("request_id"),
                "response": {}}})
    """
)


class _TuiSession:
    """The real TUI in a PTY, screen mirrored into a pyte emulator."""

    COLS, ROWS = 140, 40

    def __init__(self, tmp_path: Path, version: str | None):
        server_path = tmp_path / "fake_agent_server.py"
        server_path.write_text(FAKE_SERVER % {"version": version}, encoding="utf-8")
        env = {
            **os.environ,
            "TERM": "xterm-256color",
            "CLAWCODEX_WORKSPACE": str(tmp_path),
            "CLAWCODEX_CONFIG_DIR": str(tmp_path / "cfg"),
            "CLAWCODEX_AGENT_SERVER_CMD": json.dumps(
                [sys.executable, str(server_path)]
            ),
        }
        self.master, slave = pty.openpty()
        # Emulator and PTY must agree on geometry or wraps differ.
        import fcntl
        import struct
        import termios

        fcntl.ioctl(
            slave, termios.TIOCSWINSZ,
            struct.pack("HHHH", self.ROWS, self.COLS, 0, 0),
        )
        self.proc = subprocess.Popen(
            ["node", str(ENTRY)],
            stdin=slave, stdout=slave, stderr=slave,
            cwd=str(tmp_path), env=env, close_fds=True,
        )
        os.close(slave)
        self.screen = pyte.Screen(self.COLS, self.ROWS)
        self.stream = pyte.ByteStream(self.screen)

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            if not data:
                return
            self.stream.feed(data)

    def wait_for(self, needle: str, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.2)
            if any(needle in row for row in self.screen.display):
                return True
        return False

    def row_with(self, needle: str) -> str | None:
        for row in self.screen.display:
            if needle in row:
                return row
        return None

    def dump(self) -> str:
        return "\n".join(
            row.rstrip() for row in self.screen.display if row.strip()
        )

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()  # reap — no zombie for the rest of the run
        os.close(self.master)


def test_header_shows_the_version_the_backend_reports(tmp_path):
    tui = _TuiSession(tmp_path, BACKEND_VERSION)
    try:
        assert tui.wait_for(
            f"clawcodex v{BACKEND_VERSION}", 30
        ), f"header did not show the backend's version:\n{tui.dump()}"
    finally:
        tui.close()


def test_header_falls_back_and_still_reaches_ready_without_a_version(tmp_path):
    # A backend predating the field. The banner must still name a version and
    # the session must still go interactive — ready is gated on this string
    # being non-empty, so a blank would hang the app at "starting agent…".
    tui = _TuiSession(tmp_path, None)
    try:
        assert tui.wait_for("clawcodex v", 30), f"header missing:\n{tui.dump()}"
        row = tui.row_with("clawcodex v")
        assert row is not None
        trailing = row.split("clawcodex v", 1)[1].strip()
        assert trailing and trailing[0].isdigit(), (
            f"expected a fallback version after 'clawcodex v', got {row!r}"
        )
        assert tui.wait_for("❯", 30), f"composer never appeared:\n{tui.dump()}"
    finally:
        tui.close()

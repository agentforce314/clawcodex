"""Screen-level regression test: the recap suggestion renders as composer
ghost text BEFORE Tab, and Tab turns it into real input.

The first ship of the recap feature (#828) armed the state correctly — Tab
inserted the suggestion — but the ghost never PAINTED: the appLayout
placeholder gate keyed on ``composer.empty``, which is conversation-emptiness,
so the slot was blanked in the only state where a suggestion exists
(mid-conversation). Every state-level test passed. Only looking at the actual
terminal catches that class of bug, so this test runs the REAL TUI binary
(``ui-tui/dist/entry.js``) in a PTY against a deterministic fake agent-server
speaking the NDJSON protocol, and reads the screen with pyte:

1. wait for the composer, type a prompt, Enter;
2. fake server replies (stream → result → recap frame with a FIXED suggestion);
3. assert the suggestion text is on the composer row before any Tab;
4. Tab, then type a marker — the marker must APPEND (ghost became input;
   pre-Tab typing would replace, since a placeholder hides on input).

Skips (never fails) when the local environment can't run it: no node, no
built ``ui-tui/dist``, no pyte, or non-POSIX (pty module). The python CI job
installs no node toolchain, so there this is a documented skip; it runs for
real on dev machines.
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
# importorskip, NOT a top-level import: `pty` does not exist on Windows, and
# a module-level ImportError is a pytest COLLECTION ERROR there — the win32
# skipif marker below never gets a chance to apply (same guard as
# test_tool_system_tools.py's pty import).
pty = pytest.importorskip("pty", reason="POSIX pty required")

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty required"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH"),
    pytest.mark.skipif(not ENTRY.exists(), reason="ui-tui/dist not built"),
]

SUGGESTION = "fix all four issues and re-run"
RECAP_TEXT = "Goal was testing the ghost. Turn done. Next: accept the suggestion."

# A minimal agent-server: init frame, echo turn (stream → assistant → result),
# then the recap frame with a FIXED suggestion; every control_request gets an
# empty-object reply so startup RPCs (settings, workflow list, …) resolve.
FAKE_SERVER = textwrap.dedent(
    """
    import json, sys, threading, time

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    emit({
        "type": "system", "subtype": "init", "session_id": "s1",
        "model": "fake-model", "tools": [], "permission_mode": "default",
        "protocol_version": "0.1.0", "cwd": ".",
    })

    def turn():
        emit({"type": "stream_event", "session_id": "s1", "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "smoke ok"}}})
        emit({"type": "assistant", "session_id": "s1", "uuid": "a1",
              "message": {"role": "assistant",
                          "content": [{"type": "text", "text": "smoke ok"}]}})
        emit({"type": "result", "subtype": "success", "session_id": "s1",
              "num_turns": 1, "result": "smoke ok", "duration_ms": 5,
              "is_error": False, "usage": None, "total_cost_usd": 0.0,
              "cost": {}, "session_turns": 1})
        time.sleep(0.4)  # recap generation happens after the result
        emit({"type": "system", "subtype": "recap", "session_id": "s1",
              "recap": %(recap)r, "suggestion": %(suggestion)r})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "user":
            threading.Thread(target=turn, daemon=True).start()
        elif msg.get("type") == "control_request":
            req = msg.get("request") or {}
            emit({"type": "control_response", "response": {
                "subtype": req.get("subtype", ""),
                "request_id": msg.get("request_id"),
                "response": {}}})
    """
) % {"recap": RECAP_TEXT, "suggestion": SUGGESTION}


class _TuiSession:
    """The real TUI in a PTY, screen mirrored into a pyte emulator."""

    COLS, ROWS = 140, 40

    def __init__(self, tmp_path: Path):
        server_path = tmp_path / "fake_agent_server.py"
        server_path.write_text(FAKE_SERVER, encoding="utf-8")
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

    def send(self, text: str) -> None:
        os.write(self.master, text.encode())

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


@pytest.fixture()
def tui(tmp_path):
    session = _TuiSession(tmp_path)
    yield session
    session.close()


def test_suggestion_ghost_renders_before_tab_and_tab_accepts(tui):
    # Composer up (the ❯ prompt row paints once the client is interactive).
    assert tui.wait_for("❯", 30), f"composer never appeared:\n{tui.dump()}"

    tui.send("hello")
    assert tui.wait_for("hello", 5), f"typed text not echoed:\n{tui.dump()}"
    tui.send("\r")

    # Fake turn completes and the recap frame lands.
    assert tui.wait_for("recap:", 20), f"recap line missing:\n{tui.dump()}"
    assert tui.row_with(RECAP_TEXT.split(".")[0]) is not None

    # THE regression: the suggestion must be visible BEFORE any Tab press,
    # as ghost text in the (empty) composer.
    assert tui.wait_for(SUGGESTION, 5), (
        "suggestion ghost did not render before Tab:\n" + tui.dump()
    )

    # Tab accepts; typing then APPENDS — proving the ghost became real input
    # (pre-Tab typing would hide the placeholder and show only the marker).
    tui.send("\t")
    tui.pump(0.6)
    tui.send("XY")
    assert tui.wait_for(SUGGESTION + "XY", 5), (
        "Tab did not turn the ghost into editable input:\n" + tui.dump()
    )

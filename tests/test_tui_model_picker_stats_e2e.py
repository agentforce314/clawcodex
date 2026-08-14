"""Screen-level regression test: the stats line under the composer repoints to
the newly selected provider AND model after a /model picker switch.

#836 threaded `provider` end to end so a cross-provider selection could not
leave `anthropic · gpt-5.6-luna` on the row — and every state-level test for it
passed. It also gave the picker a third step (effort), which made
``onModelSelect`` dispatch ``/model …`` and ``/effort …`` back-to-back in ONE
tick. ``createSlashHandler`` kept a single "flight" counter shared by every
slash, so the second dispatch marked the first superseded and
``guarded`` discarded the ``/model`` reply — the one that folds provider+model
into ``ui.info``. The backend still switched, so the session answered from
DeepSeek while the row kept reading ``anthropic · claude-opus-5``.

The bug only exists when both commands land in the same tick, which no typed
input can produce — it needs the real picker driving the real handler. So this
runs the REAL TUI binary (``ui-tui/dist/entry.js``) in a PTY against a
deterministic fake agent-server speaking NDJSON, walks the three picker steps
with the keyboard, and reads the row back with pyte.

Skips (never fails) when the local environment can't run it: no node, no built
``ui-tui/dist``, no pyte, or non-POSIX (pty module). The python CI job installs
no node toolchain, so there this is a documented skip; it runs on dev machines.
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
# importorskip, NOT a top-level import: `pty` does not exist on Windows, and a
# module-level ImportError is a pytest COLLECTION ERROR there — the win32
# skipif marker below never gets a chance to apply.
pty = pytest.importorskip("pty", reason="POSIX pty required")

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty required"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH"),
    pytest.mark.skipif(not ENTRY.exists(), reason="ui-tui/dist not built"),
]

OLD_MODEL = "claude-opus-5"
OLD_PROVIDER = "anthropic"
NEW_MODEL = "deepseek-v4-flash"
NEW_PROVIDER = "deepseek"

# A minimal agent-server: an init frame that seeds the stats line with the OLD
# pairing, a one-provider/one-model picker so the arrow keys have nothing to
# disambiguate, a full effort ladder so step 3 renders (that third step is what
# fires the second dispatch), and a set_model that echoes the new provider the
# way agent_server._do_set_model does. Every other control_request gets an
# empty-object reply so startup RPCs resolve.
FAKE_SERVER = textwrap.dedent(
    """
    import json, sys

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    emit({
        "type": "system", "subtype": "init", "session_id": "s1",
        "model": %(old_model)r, "provider": %(old_provider)r,
        "tools": [], "permission_mode": "default",
        "protocol_version": "0.1.0", "cwd": ".",
    })

    def answer(subtype):
        if subtype == "list_model_providers":
            return {
                "ok": True,
                "model": %(old_model)r,
                "provider": %(old_provider)r,
                "providers": [{
                    "slug": %(new_provider)r,
                    "name": %(new_provider)r,
                    "authenticated": True,
                    "models": [%(new_model)r],
                    "total_models": 1,
                }],
            }
        if subtype == "effort_options":
            # `current` must name a REAL level, not "". Step 3 lists
            # [auto, *levels] and lands on the session's live level, and the
            # picker only emits `/effort` when the chosen row is not `auto` —
            # so a session sitting on `auto` dispatches ONE command and cannot
            # arm this regression at all. The reported session was on `max`,
            # which is why plain Enter-through hit it.
            return {
                "ok": True,
                "supported": True,
                "levels": ["low", "medium", "high"],
                "current": "high",
            }
        if subtype == "set_model":
            # The shape #836 settled on: echo the provider beside the model.
            return {"ok": True, "model": %(new_model)r, "provider": %(new_provider)r}
        return {}

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
                "response": answer(req.get("subtype", "")),
            }})
    """
) % {
    "old_model": OLD_MODEL,
    "old_provider": OLD_PROVIDER,
    "new_model": NEW_MODEL,
    "new_provider": NEW_PROVIDER,
}


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


def test_stats_line_repoints_to_the_picked_provider_and_model(tui):
    # Composer up (the ❯ prompt row paints once the client is interactive).
    assert tui.wait_for("❯", 30), f"composer never appeared:\n{tui.dump()}"

    # The stats line starts on the session's init pairing — this is the
    # "before" the assertion at the bottom has to move off, so a picker that
    # silently no-ops cannot pass by accident.
    assert tui.wait_for(OLD_PROVIDER, 10), (
        f"stats line never showed the initial provider:\n{tui.dump()}"
    )
    before = tui.row_with(f"{OLD_PROVIDER} · {OLD_MODEL}")
    assert before is not None, (
        f"stats line did not start at '{OLD_PROVIDER} · {OLD_MODEL}':\n{tui.dump()}"
    )

    # Open the picker and walk its three steps. One provider and one model, so
    # Enter alone selects each; step 3 lands preselected on `high` (the fake's
    # `current`), so Enter there emits `/effort high` alongside `/model` — the
    # two-dispatches-in-one-tick that arms the regression.
    tui.send("/model")
    tui.pump(0.5)
    tui.send("\r")
    assert tui.wait_for(NEW_PROVIDER, 10), f"picker never listed the provider:\n{tui.dump()}"

    tui.send("\r")  # step 1 → provider
    assert tui.wait_for(NEW_MODEL, 10), f"picker never listed the model:\n{tui.dump()}"

    tui.send("\r")  # step 2 → model
    assert tui.wait_for("low", 10), f"effort step never rendered:\n{tui.dump()}"

    tui.send("\r")  # step 3 → effort, which applies the switch

    # THE regression: the row must name the provider AND model just selected.
    assert tui.wait_for(f"{NEW_PROVIDER} · {NEW_MODEL}", 15), (
        "stats line did not repoint after the picker switch "
        f"(expected '{NEW_PROVIDER} · {NEW_MODEL}'):\n{tui.dump()}"
    )

    # And neither stale half may survive anywhere on the row.
    row = tui.row_with(f"{NEW_PROVIDER} · {NEW_MODEL}")
    assert row is not None
    assert OLD_PROVIDER not in row, f"stale provider still on the stats row: {row!r}"
    assert OLD_MODEL not in row, f"stale model still on the stats row: {row!r}"

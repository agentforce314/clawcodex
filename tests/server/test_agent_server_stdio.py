"""Integration test for the agent-server ``--stdio`` transport.

The local TUI link talks to the Python agent-server over the child's
stdin/stdout (NDJSON) instead of a WebSocket — a pipe can't idle-time-out, which
is the whole point (a WS would silently disconnect after an idle period). This
spawns the real subprocess and asserts the lifecycle:

  * ``system/init`` is emitted on stdout (the stdio pump works), and stdout
    carries JSON frames only (the startup banner is routed to stderr).
  * closing stdin (the parent going away) exits the process.

It is heavier than the unit suite (imports the agent stack once), but it guards
the user-facing transport against regressions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_stdio_emits_init_and_exits_on_stdin_close(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(_REPO), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "src.entrypoints.agent_server_cli",
            "--stdio", "--permission-mode", "bypassPermissions",
            "--workspace", str(tmp_path),
        ],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=env, cwd=str(_REPO),
    )

    frames: list[dict] = []
    non_json: list[str] = []

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            s = line.strip()
            if not s:
                continue
            try:
                frames.append(json.loads(s))
            except json.JSONDecodeError:
                non_json.append(s)

    threading.Thread(target=_reader, daemon=True).start()

    try:
        # 1) system/init shows up on stdout within a generous startup window.
        deadline = time.time() + 50
        while time.time() < deadline and not any(
            f.get("type") == "system" and f.get("subtype") == "init" for f in frames
        ):
            time.sleep(0.2)
        assert any(
            f.get("type") == "system" and f.get("subtype") == "init" for f in frames
        ), f"no system/init frame on stdout (got types={[f.get('type') for f in frames]})"

        # stdout is reserved for JSON frames — the banner must NOT pollute it.
        assert not non_json, f"non-JSON lines on stdout: {non_json[:3]}"

        # 2) closing stdin (parent gone) ends the session.
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=15)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

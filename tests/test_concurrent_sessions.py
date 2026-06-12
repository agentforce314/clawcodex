"""#284 — concurrent-session PID registry (port of utils/concurrentSessions.ts).

Covers registration, the bridge-ID publish/clear cycle that lets peers
dedup a session reachable over both UDS and bridge, live-session
counting with stale-file sweeping, and the strict ``<pid>.json``
filename guard (TS issue #34210: a lenient prefix-parse swept unrelated
files as "stale").
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.utils import concurrent_sessions as cs


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    import src.config as config_mod

    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_DIR", tmp_path / ".clawcodex")
    cs.reset_for_testing()
    yield
    cs.reset_for_testing()


def _pid_file(tmp_path: Path) -> Path:
    return tmp_path / ".clawcodex" / "sessions" / f"{os.getpid()}.json"


class TestRegisterSession:
    def test_register_writes_pid_file(self, tmp_path):
        assert cs.register_session() is True
        data = json.loads(_pid_file(tmp_path).read_text())
        assert data["pid"] == os.getpid()
        assert data["sessionId"]
        assert data["cwd"]
        assert data["kind"] == "interactive"

    def test_register_is_idempotent(self, tmp_path):
        assert cs.register_session() is True
        before = _pid_file(tmp_path).read_text()
        assert cs.register_session() is True
        assert _pid_file(tmp_path).read_text() == before

    def test_teammates_are_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_AGENT_ID", "a123")
        assert cs.register_session() is False
        assert not _pid_file(tmp_path).exists()

    def test_session_switch_updates_pid_file(self, tmp_path):
        from src.bootstrap.state import _session_switched

        cs.register_session()
        _session_switched.emit("switched-session-id")
        data = json.loads(_pid_file(tmp_path).read_text())
        assert data["sessionId"] == "switched-session-id"


class TestBridgeIdPublish:
    def test_publish_and_clear_round_trip(self, tmp_path):
        cs.register_session()
        cs.update_session_bridge_id("session_abc123")
        data = json.loads(_pid_file(tmp_path).read_text())
        assert data["bridgeSessionId"] == "session_abc123"

        # Cleared on bridge teardown so a stale ID doesn't suppress a
        # legitimately-remote session after reconnect.
        cs.update_session_bridge_id(None)
        data = json.loads(_pid_file(tmp_path).read_text())
        assert data["bridgeSessionId"] is None

    def test_update_without_registration_is_noop(self, tmp_path):
        cs.update_session_bridge_id("session_abc123")  # must not raise
        assert not _pid_file(tmp_path).exists()

    def test_set_repl_bridge_handle_publishes_compat_id(self, tmp_path):
        from src.bridge import repl_bridge_handle as rbh

        cs.register_session()

        class _Handle:
            bridge_session_id = "cse_deadbeef"

        try:
            rbh.set_repl_bridge_handle(_Handle())
            data = json.loads(_pid_file(tmp_path).read_text())
            assert data["bridgeSessionId"] == rbh.get_self_bridge_compat_id()
            assert data["bridgeSessionId"].startswith("session_")

            rbh.set_repl_bridge_handle(None)
            data = json.loads(_pid_file(tmp_path).read_text())
            assert data["bridgeSessionId"] is None
        finally:
            rbh.set_repl_bridge_handle(None)


class TestUpdateSessionName:
    def test_name_round_trip(self, tmp_path):
        cs.register_session()
        cs.update_session_name("my-session")
        data = json.loads(_pid_file(tmp_path).read_text())
        assert data["name"] == "my-session"

    def test_falsy_name_is_noop(self, tmp_path):
        cs.register_session()
        before = _pid_file(tmp_path).read_text()
        cs.update_session_name(None)
        cs.update_session_name("")
        assert _pid_file(tmp_path).read_text() == before


class TestCountConcurrentSessions:
    def test_counts_self(self):
        cs.register_session()
        assert cs.count_concurrent_sessions() == 1

    def test_missing_dir_returns_zero(self):
        assert cs.count_concurrent_sessions() == 0

    def test_stale_pid_file_is_swept(self, tmp_path):
        cs.register_session()
        sessions = tmp_path / ".clawcodex" / "sessions"
        # A PID that cannot exist (max pid is bounded well below this).
        stale = sessions / "999999999.json"
        stale.write_text("{}")
        assert cs.count_concurrent_sessions() == 1
        assert not stale.exists()

    def test_live_peer_pid_is_counted(self, tmp_path):
        cs.register_session()
        sessions = tmp_path / ".clawcodex" / "sessions"
        # Our parent (the test runner) is a genuinely live peer PID.
        (sessions / f"{os.getppid()}.json").write_text("{}")
        assert cs.count_concurrent_sessions() == 2

    def test_pid_one_and_zero_are_never_counted_live(self, tmp_path):
        # TS isProcessRunning: pid <= 1 -> false. Signal 0 to pid 0 hits
        # our own process group (always succeeds) and init/launchd would
        # otherwise count as a live session forever.
        cs.register_session()
        sessions = tmp_path / ".clawcodex" / "sessions"
        (sessions / "1.json").write_text("{}")
        (sessions / "0.json").write_text("{}")
        assert cs.count_concurrent_sessions() == 1
        assert not (sessions / "1.json").exists()  # swept as stale
        assert not (sessions / "0.json").exists()

    def test_non_pid_filenames_are_never_swept(self, tmp_path):
        # TS issue #34210: parseInt's prefix-parse swept
        # "2026-03-14_notes.md" as PID 2026 — silent user data loss.
        cs.register_session()
        sessions = tmp_path / ".clawcodex" / "sessions"
        bystander = sessions / "2026-03-14_notes.md"
        bystander.write_text("important")
        assert cs.count_concurrent_sessions() == 1
        assert bystander.exists()
        assert bystander.read_text() == "important"

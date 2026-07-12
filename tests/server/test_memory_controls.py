"""Agent-server /memory wiring: the ``memory_targets`` control serializes the
shared ``build_memory_options`` hierarchy (synthetic User/Project rows plus
loaded files) for the TUI picker overlay, and ``memory_edited`` busts the
memory-file cache after the TUI's ``$EDITOR`` spawn so the next turn re-reads
disk. Covers: the two synthetic rows with TS-verbatim descriptions, the
exception guard, and the cache bust."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.server.agent_server import AgentServerConfig, _AgentSession


def _make_session(cwd: str) -> tuple[_AgentSession, list[dict]]:
    emitted: list[dict] = []
    sess = _AgentSession(
        session_id="memory-sess", cwd=cwd,
        config=AgentServerConfig(single_session=True),
        loop=MagicMock(), out_queue=MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)  # type: ignore[method-assign]
    return sess, emitted


def _control(sess: _AgentSession, subtype: str, **params) -> None:
    asyncio.run(sess._handle_control_request({
        "type": "control_request",
        "request_id": "req-1",
        "request": {"subtype": subtype, **params},
    }))


def _last_reply(emitted: list[dict]) -> dict:
    for env in reversed(emitted):
        if env.get("type") == "control_response":
            return env["response"]["response"]
    raise AssertionError(f"no control_response in {emitted!r}")


async def _no_files(cwd=None, **kwargs):
    return []


class TestMemoryTargetsControl(unittest.TestCase):
    def test_targets_lead_with_synthetic_user_and_project_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mem_ctl_") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            cwd = Path(tmp) / "proj"
            cwd.mkdir()
            with (
                patch("pathlib.Path.home", classmethod(lambda cls: home)),
                patch("src.context_system.claude_md.get_memory_files", _no_files),
                patch("src.utils.git.get_repo_root", lambda *a, **k: None),
            ):
                sess, emitted = _make_session(str(cwd))
                _control(sess, "memory_targets")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            targets = reply["targets"]
            self.assertEqual(targets[0]["label"], "User memory")
            self.assertEqual(targets[0]["path"], str(home / ".clawcodex" / "CLAUDE.md"))
            self.assertEqual(targets[0]["description"], "Saved in ~/.clawcodex/CLAUDE.md")
            self.assertEqual(targets[1]["label"], "Project memory")
            self.assertEqual(targets[1]["path"], str(cwd / "CLAUDE.md"))
            self.assertEqual(targets[1]["description"], "Saved in ./CLAUDE.md")

    def test_targets_error_guard_replies_clean_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mem_ctl_") as tmp:
            with patch(
                "src.command_system.memory_command.build_memory_options",
                side_effect=RuntimeError("boom"),
            ):
                sess, emitted = _make_session(tmp)
                _control(sess, "memory_targets")
            reply = _last_reply(emitted)
            self.assertFalse(reply["ok"])
            self.assertIn("boom", reply["error"])
            self.assertEqual(reply["targets"], [])

    def test_memory_edited_busts_memory_file_cache(self) -> None:
        import src.context_system.claude_md as claude_md

        with tempfile.TemporaryDirectory(prefix="mem_ctl_") as tmp:
            saved = claude_md._memory_files_cache
            try:
                claude_md._memory_files_cache = ("stale-key", [])
                sess, emitted = _make_session(tmp)
                _control(sess, "memory_edited")
                self.assertTrue(_last_reply(emitted)["ok"])
                self.assertIsNone(claude_md._memory_files_cache)
            finally:
                claude_md._memory_files_cache = saved


if __name__ == "__main__":
    unittest.main()

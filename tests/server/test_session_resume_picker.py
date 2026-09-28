"""The TUI's resume picker is served by ``list_sessions`` and ``resume``.

The picker showed "0 resumable" in every workspace: the client never asked,
and the listing it would have asked for came back unsorted — session files
carry ``updated_at`` as a float or, from the older writer, an ISO string, and
sorting the mix raised inside a catch-all.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest

from src.agent.conversation import Conversation
from src.types.messages import AssistantMessage, UserMessage


def _session(cwd: str, session_id: str = "live"):
    from src.server.agent_server import AgentServerConfig, _AgentSession

    emitted: list = []
    sess = _AgentSession(
        session_id=session_id,
        cwd=cwd,
        config=AgentServerConfig(single_session=True),
        loop=mock.MagicMock(),
        out_queue=mock.MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)
    return sess, emitted


def _reply_of(emitted: list) -> dict:
    return emitted[-1]["response"]["response"]


@pytest.fixture()
def sessions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Everything a resume touches (cost restore reads the config dir too)
    # stays under tmp_path, never the developer's real ~/.clawcodex.
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path / "config"))
    directory = tmp_path / "sessions"
    directory.mkdir()
    monkeypatch.setattr("src.server.agent_server._sessions_dir", lambda: directory)
    return directory


def _save(directory: Path, session_id: str, **fields) -> None:
    data = {"session_id": session_id, "conversation": {"messages": []}, **fields}
    (directory / f"{session_id}.json").write_text(json.dumps(data), encoding="utf-8")


def test_saved_sessions_list_newest_first_across_both_timestamp_formats(sessions_dir: Path) -> None:
    from src.server.agent_server import _list_saved_sessions

    # Names sort opposite to their ages, so an unsorted (directory-order)
    # listing cannot pass by accident.
    _save(sessions_dir, "a-oldest", updated_at=1_700_000_000.0)
    _save(sessions_dir, "b-iso", updated_at="2024-06-01T00:00:00")
    _save(sessions_dir, "c-newer", updated_at=1_790_000_000.0)
    _save(sessions_dir, "d-newest-iso", updated_at="2026-12-31T23:00:00Z")
    (sessions_dir / "e-garbage.json").write_text("{not json", encoding="utf-8")

    rows = _list_saved_sessions(10)

    assert [row["session_id"] for row in rows] == ["d-newest-iso", "c-newer", "b-iso", "a-oldest"]
    assert all(isinstance(row["updated_at"], float) for row in rows)


def test_a_non_finite_timestamp_cannot_break_the_listing_reply(sessions_dir: Path) -> None:
    from src.server.agent_server import _list_saved_sessions

    (sessions_dir / "nan.json").write_text('{"session_id": "nan", "updated_at": NaN}', encoding="utf-8")
    _save(sessions_dir, "fine", updated_at=1.0)

    rows = _list_saved_sessions(10)

    # NaN would sort unpredictably and make the NDJSON reply invalid JSON.
    json.dumps(rows, allow_nan=False)
    assert {row["session_id"] for row in rows} == {"nan", "fine"}


def test_list_sessions_clamps_its_limit(tmp_path: Path, sessions_dir: Path) -> None:
    for n in range(3):
        _save(sessions_dir, f"s{n}", cwd=str(tmp_path), updated_at=float(n))
    sess, emitted = _session(str(tmp_path))

    asyncio.run(sess._handle_control_request(
        {"request_id": "r1", "request": {"subtype": "list_sessions", "limit": 0}}
    ))

    assert [row["session_id"] for row in _reply_of(emitted)["sessions"]] == ["s2"]


def test_list_sessions_keeps_this_workspace_and_leaves_out_the_live_session(
    tmp_path: Path, sessions_dir: Path
) -> None:
    here = str(tmp_path / "project")
    _save(sessions_dir, "mine", cwd=here, updated_at=3.0, message_count=788, preview="prove it")
    _save(sessions_dir, "live", cwd=here, updated_at=4.0)
    _save(sessions_dir, "elsewhere", cwd=str(tmp_path / "other"), updated_at=5.0)
    _save(sessions_dir, "no-cwd", updated_at=6.0)
    sess, emitted = _session(here, session_id="live")

    asyncio.run(sess._handle_control_request(
        {"request_id": "r1", "request": {"subtype": "list_sessions", "cwd": here + "/", "limit": 50}}
    ))

    reply = _reply_of(emitted)
    assert [row["session_id"] for row in reply["sessions"]] == ["mine"]
    assert reply["sessions"][0]["message_count"] == 788
    assert reply["sessions"][0]["preview"] == "prove it"


def test_list_sessions_without_a_cwd_still_lists_every_workspace(tmp_path: Path, sessions_dir: Path) -> None:
    _save(sessions_dir, "one", cwd=str(tmp_path / "a"), updated_at=1.0)
    _save(sessions_dir, "two", cwd=str(tmp_path / "b"), updated_at=2.0)
    sess, emitted = _session(str(tmp_path))

    asyncio.run(sess._handle_control_request({"request_id": "r1", "request": {"subtype": "list_sessions"}}))

    assert [row["session_id"] for row in _reply_of(emitted)["sessions"]] == ["two", "one"]


def _saved_conversation(directory: Path) -> None:
    conv = Conversation()
    conv.messages.extend([
        UserMessage(content="prove the lemma"),
        UserMessage(content="<system-reminder>Messages from your teammates follow.</system-reminder>"),
        UserMessage(content="hook context the model saw but the user never typed", isMeta=True),
        AssistantMessage(content=[
            {"type": "openai_responses_item", "item": {"type": "reasoning", "encrypted_content": "x"}},
            {"type": "text", "text": "Reading the notes first."},
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "/w/notes.md", "offset": 10}},
        ]),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "t1", "content": "1\tbody"}]),
        AssistantMessage(content=[
            {"type": "tool_use", "id": "t2", "name": "Write",
             "input": {"file_path": "/w/proof.lean", "content": "x" * 50_000}},
        ]),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]),
        AssistantMessage(content=[{"type": "text", "text": "The lemma holds."}]),
        UserMessage(content="summary of earlier turns", isCompactSummary=True),
        UserMessage(content=[{"type": "text", "text": "[Image #1] now the multiple-of-four case"}]),
    ])
    _save(directory, "old", conversation=conv.to_dict(), cwd=str(directory.parent), name="Liouville run")


def test_resume_can_return_the_conversation_as_transcript_rows(tmp_path: Path, sessions_dir: Path) -> None:
    _saved_conversation(sessions_dir)
    sess, emitted = _session(str(tmp_path))
    sess.session = mock.MagicMock()

    sess._do_resume("r", "old", include_messages=True)

    reply = _reply_of(emitted)
    assert reply["ok"] is True
    assert reply["messages"] == [
        {"role": "user", "text": "prove the lemma"},
        {"role": "assistant", "text": "Reading the notes first."},
        {"role": "tool", "name": "Read", "input": {"file_path": "/w/notes.md"}},
        {"role": "tool", "name": "Write", "input": {"file_path": "/w/proof.lean"}},
        {"role": "assistant", "text": "The lemma holds."},
        {"role": "user", "text": "[Image #1] now the multiple-of-four case"},
    ]


def test_resume_without_the_flag_keeps_its_old_reply(tmp_path: Path, sessions_dir: Path) -> None:
    _saved_conversation(sessions_dir)
    sess, emitted = _session(str(tmp_path))
    sess.session = mock.MagicMock()

    sess._do_resume("r", "old")

    reply = _reply_of(emitted)
    assert reply["ok"] is True and reply["count"] == 10
    assert "messages" not in reply


def test_resume_finds_a_renamed_session_in_this_workspace_by_its_title(
    tmp_path: Path, sessions_dir: Path
) -> None:
    _saved_conversation(sessions_dir)
    sess, emitted = _session(str(tmp_path))
    sess.session = mock.MagicMock()

    def resume(session, target: str) -> None:
        asyncio.run(session._handle_control_request({"request_id": "r", "request": {
            "subtype": "resume", "session_id": target, "include_messages": True}}))

    resume(sess, "liouville RUN")

    assert _reply_of(emitted)["messages"][0] == {"role": "user", "text": "prove the lemma"}

    elsewhere, emitted_elsewhere = _session(str(tmp_path / "other-project"))
    elsewhere.session = mock.MagicMock()
    resume(elsewhere, "Liouville run")
    assert _reply_of(emitted_elsewhere) == {"ok": False, "error": "session not found"}


def test_a_blank_resume_target_matches_no_unnamed_session(tmp_path: Path, sessions_dir: Path) -> None:
    _save(sessions_dir, "unnamed", cwd=str(tmp_path), updated_at=1.0)
    sess, emitted = _session(str(tmp_path))
    sess.session = mock.MagicMock()
    sess.session.conversation = "untouched"

    asyncio.run(sess._handle_control_request(
        {"request_id": "r", "request": {"subtype": "resume", "session_id": "   "}}
    ))

    assert _reply_of(emitted) == {"ok": False, "error": "session not found"}
    assert sess.session.conversation == "untouched"


def test_resume_never_builds_a_path_from_an_unsafe_id(tmp_path: Path, sessions_dir: Path) -> None:
    (tmp_path / "outside.json").write_text(json.dumps({"conversation": {"messages": []}}), encoding="utf-8")
    sess, emitted = _session(str(tmp_path))
    sess.session = mock.MagicMock()
    sess.session.conversation = "untouched"

    sess._do_resume("r", "../outside")

    assert _reply_of(emitted) == {"ok": False, "error": "session not found"}
    assert sess.session.conversation == "untouched"


def test_resume_carries_the_restored_scheduled_tasks_notice(tmp_path: Path, sessions_dir: Path) -> None:
    from src.scheduled_tasks import SessionCronScheduler

    donor = SessionCronScheduler(jitter=False)
    donor.create("*/5 * * * *", "check the build")
    _save(sessions_dir, "old", scheduled_tasks=donor.snapshot())
    sess, emitted = _session(str(tmp_path))
    sess.session = mock.MagicMock()
    sess.cron_scheduler = SessionCronScheduler(jitter=False)

    sess._do_resume("r", "old", include_messages=True)

    # Also pushed as its own cron_status line, which the TUI's repaint clears.
    assert _reply_of(emitted)["cron_notice"] == "⏰ Restored 1 scheduled task(s) from the saved session."


def test_delete_session_removes_a_saved_session_but_never_the_live_one(
    tmp_path: Path, sessions_dir: Path
) -> None:
    _save(sessions_dir, "old")
    _save(sessions_dir, "live")
    sess, emitted = _session(str(tmp_path), session_id="live")

    def delete(target: str) -> dict:
        asyncio.run(sess._handle_control_request(
            {"request_id": "r", "request": {"subtype": "delete_session", "session_id": target}}
        ))
        return _reply_of(emitted)

    assert delete("old") == {"ok": True, "deleted": "old"}
    assert not (sessions_dir / "old.json").exists()
    assert delete("live") == {"ok": False, "error": "cannot delete the live session"}
    assert (sessions_dir / "live.json").exists()
    assert delete("../live") == {"ok": False, "error": "session not found"}
    assert delete("old") == {"ok": False, "error": "session not found"}

"""WI-6.3 tests — JSONL mailbox + path-traversal sanitization.

Covers:
* Path sanitization (critic concern C1) — `..`, `/`, empty, oversize.
* Atomic O_APPEND writes; concurrent writers don't interleave.
* Read tolerance of partial trailing lines and blank lines.
* Envelope helpers (shutdown_request/response, plan_approval_response).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from src.services.swarm.mailbox import (
    TeammateMessage,
    create_plan_approval_response_message,
    create_shutdown_approved_message,
    create_shutdown_rejected_message,
    create_shutdown_request_message,
    get_inbox_path,
    get_mailboxes_root,
    make_iso_timestamp,
    read_mailbox,
    write_to_mailbox,
)


# ---------------------------------------------------------------------------
# Path sanitization — concern C1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        # Path-traversal payloads.
        "..", "../etc/passwd", "../../etc/passwd", "evil/path",
        # Whitespace / control / null.
        "with space", "name with\nbreaks", "\x00null-byte", "tab\there",
        # Backslash (Windows-flavor traversal vector + just disallowed).
        "name\\back", "back\\\\slash",
        # Leading / trailing dots — disallowed even if no traversal,
        # to keep the whitelist crisp (per N1 fold-in).
        ".alice", "alice.", ".", "..",
        # Empty / oversize.
        "", "a" * 65, "a" * 100,
        # Unicode is rejected by the ASCII-only whitelist.
        "🙂unicode", "café",
    ],
)
def test_recipient_traversal_rejected(tmp_path: Path, bad_name: str) -> None:
    """Bad names raise ``ValueError`` BEFORE any filesystem operation."""
    with pytest.raises(ValueError, match="Invalid recipient_name"):
        get_inbox_path(bad_name, "team", tmp_path)


@pytest.mark.parametrize(
    "bad_name",
    ["..", "../other-team", "team/with-slash", "", "a" * 65],
)
def test_team_traversal_rejected(tmp_path: Path, bad_name: str) -> None:
    with pytest.raises(ValueError, match="Invalid team_name"):
        get_inbox_path("recipient", bad_name, tmp_path)


@pytest.mark.parametrize(
    "good_name",
    ["alice", "bob_42", "team-lead", "x", "X", "a-very-long-but-still-valid-team-name-of-44-len"],
)
def test_legal_names_accepted(tmp_path: Path, good_name: str) -> None:
    """Whitelist accepts ``[A-Za-z0-9_-]{1,64}``."""
    path = get_inbox_path(good_name, good_name, tmp_path)
    assert path.suffix == ".jsonl"


def test_inbox_path_isolated_per_team(tmp_path: Path) -> None:
    p1 = get_inbox_path("alice", "team-a", tmp_path)
    p2 = get_inbox_path("alice", "team-b", tmp_path)
    assert p1 != p2
    assert p1.parent != p2.parent


# ---------------------------------------------------------------------------
# Round-trip — write then read
# ---------------------------------------------------------------------------


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    msg = TeammateMessage(
        from_="alice", text="hello bob", timestamp="2026-05-08T12:00:00Z",
        summary="greeting",
    )
    write_to_mailbox("bob", msg, team_name="t", workspace_root=tmp_path)

    messages = read_mailbox("bob", team_name="t", workspace_root=tmp_path)
    assert len(messages) == 1
    assert messages[0].from_ == "alice"
    assert messages[0].text == "hello bob"
    assert messages[0].summary == "greeting"


def test_multiple_writes_preserve_order(tmp_path: Path) -> None:
    for i in range(5):
        msg = TeammateMessage(
            from_="x", text=f"msg-{i}", timestamp="2026-05-08T12:00:00Z",
        )
        write_to_mailbox("inbox", msg, team_name="t", workspace_root=tmp_path)

    messages = read_mailbox("inbox", team_name="t", workspace_root=tmp_path)
    assert [m.text for m in messages] == [f"msg-{i}" for i in range(5)]


def test_read_missing_inbox_returns_empty(tmp_path: Path) -> None:
    messages = read_mailbox("never-wrote", team_name="t", workspace_root=tmp_path)
    assert messages == []


def test_inbox_file_has_user_only_permissions(tmp_path: Path) -> None:
    """0o600 — mailboxes can carry sensitive plan content."""
    msg = TeammateMessage(from_="x", text="x", timestamp="2026-05-08T12:00:00Z")
    write_to_mailbox("alice", msg, team_name="t", workspace_root=tmp_path)
    path = get_inbox_path("alice", "t", tmp_path)
    mode = os.stat(path).st_mode & 0o777
    assert mode & 0o077 == 0, f"mailbox leaks permissions: {oct(mode)}"


# ---------------------------------------------------------------------------
# Reader tolerance
# ---------------------------------------------------------------------------


def test_reader_tolerates_partial_trailing_line(tmp_path: Path) -> None:
    path = get_inbox_path("alice", "t", tmp_path)
    path.write_bytes(
        b'{"from":"x","text":"good","timestamp":"t"}\n'
        b'{"from":"y","text":"good","timestamp":"t"}\n'
        b'{"from":"z","text":"part'  # truncated mid-write
    )
    messages = read_mailbox("alice", team_name="t", workspace_root=tmp_path)
    assert len(messages) == 2
    assert messages[0].text == "good"


def test_reader_skips_blank_lines(tmp_path: Path) -> None:
    path = get_inbox_path("alice", "t", tmp_path)
    path.write_text(
        '{"from":"a","text":"x","timestamp":"t"}\n'
        '\n\n'
        '{"from":"b","text":"y","timestamp":"t"}\n',
        encoding="utf-8",
    )
    messages = read_mailbox("alice", team_name="t", workspace_root=tmp_path)
    assert len(messages) == 2


# ---------------------------------------------------------------------------
# Concurrent writers — atomic O_APPEND at line granularity
# ---------------------------------------------------------------------------


def test_concurrent_writes_no_interleave(tmp_path: Path) -> None:
    n_threads = 4
    n_writes = 50

    def worker(idx: int) -> None:
        for j in range(n_writes):
            msg = TeammateMessage(
                from_=f"t{idx}", text=f"thread-{idx}-msg-{j}",
                timestamp="2026-05-08T12:00:00Z",
            )
            write_to_mailbox("inbox", msg, team_name="t", workspace_root=tmp_path)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    messages = read_mailbox("inbox", team_name="t", workspace_root=tmp_path)
    assert len(messages) == n_threads * n_writes
    pairs = {(m.from_, m.text) for m in messages}
    assert len(pairs) == n_threads * n_writes


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def test_shutdown_request_envelope_shape() -> None:
    msg = create_shutdown_request_message(
        request_id="req-1", from_="leader", reason="end of session"
    )
    assert msg["type"] == "shutdown_request"
    assert msg["request_id"] == "req-1"
    assert msg["from"] == "leader"
    assert msg["reason"] == "end of session"
    assert "timestamp" in msg


def test_shutdown_request_envelope_omits_optional_reason() -> None:
    msg = create_shutdown_request_message(request_id="req-1", from_="leader")
    assert "reason" not in msg


def test_shutdown_approved_envelope_shape() -> None:
    msg = create_shutdown_approved_message(request_id="req-1", from_="bob")
    assert msg["type"] == "shutdown_response"
    assert msg["approved"] is True
    assert "reason" not in msg


def test_shutdown_rejected_envelope_shape() -> None:
    msg = create_shutdown_rejected_message(
        request_id="req-1", from_="bob", reason="finishing migration"
    )
    assert msg["approved"] is False
    assert msg["reason"] == "finishing migration"


def test_plan_approval_response_envelope_shape() -> None:
    msg = create_plan_approval_response_message(
        request_id="req-1", approved=True,
        permission_mode="default", from_="team-lead",
    )
    assert msg["type"] == "plan_approval_response"
    assert msg["approved"] is True
    assert msg["permission_mode"] == "default"
    assert msg["from"] == "team-lead"


def test_plan_approval_rejection_carries_feedback() -> None:
    msg = create_plan_approval_response_message(
        request_id="req-1", approved=False,
        permission_mode="default", from_="team-lead",
        feedback="needs more research",
    )
    assert msg["approved"] is False
    assert msg["feedback"] == "needs more research"


def test_iso_timestamp_format() -> None:
    ts = make_iso_timestamp()
    # ISO-8601 UTC with Z suffix — basic shape check.
    assert ts.endswith("Z")
    assert "T" in ts

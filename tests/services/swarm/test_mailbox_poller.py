"""WI-9.1 tests — mailbox poller dispatch + receiver-side defense-in-depth.

Covers:
* Plain-text messages → ``pending_user_messages``.
* ``shutdown_request`` → ``shutdown_requested=True`` on teammate state.
* ``plan_approval_response`` from lead → flips ``awaiting_plan_approval``
  + sets ``permission_mode``.
* **Plan-approval from non-lead → log-and-drop** (Chunk-F D1 deferral
  + critic concern C3 receiver-side defense-in-depth).
* ``permission_response`` envelope → ``deliver_permission_decision``.
* Read-offset cursor advances; restarts resume cleanly.
* Daemon thread lifecycle.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from src.services.swarm.leader_permission_bridge import (
    _reset_callbacks,
    register_permission_callback,
)
from src.services.swarm.mailbox import TeammateMessage, write_to_mailbox
from src.services.swarm.mailbox_poller import (
    start_mailbox_poller,
    stop_mailbox_poller,
    sweep_mailboxes,
)
from src.task_registry import RuntimeTaskRegistry
from src.tasks.in_process_teammate import (
    InProcessTeammateTaskState,
    TeammateIdentity,
)


@pytest.fixture(autouse=True)
def _clear_callbacks() -> None:
    _reset_callbacks()
    yield
    _reset_callbacks()
    # Tear down any sweeper thread the test may have started.
    stop_mailbox_poller(timeout=1.0)


def _make_teammate(reg: RuntimeTaskRegistry, agent_id: str = "t1") -> str:
    """Seed an in-process teammate state on the registry. Returns id."""
    state = InProcessTeammateTaskState(
        id=agent_id,
        type="in_process_teammate",
        status="running",
        description="x",
        start_time=0.0,
        output_file="/tmp/x",
        identity=TeammateIdentity(
            agent_id=agent_id, agent_name="alice",
            team_name="t",
        ),
    )
    reg.upsert(state)
    return agent_id


def _write_envelope(
    *, recipient: str, envelope: dict, sender: str, tmp_path: Path
) -> None:
    msg = TeammateMessage(
        from_=sender,
        text=json.dumps(envelope, ensure_ascii=False),
        timestamp="2026-05-08T12:00:00Z",
    )
    write_to_mailbox(
        recipient, msg, team_name="t", workspace_root=tmp_path,
    )


# ---------------------------------------------------------------------------
# Plain-text → pending_user_messages
# ---------------------------------------------------------------------------


def test_plain_text_message_goes_to_pending_user_messages(tmp_path: Path) -> None:
    reg = RuntimeTaskRegistry()
    agent_id = _make_teammate(reg)

    msg = TeammateMessage(
        from_="leader", text="hi alice", timestamp="2026-05-08T12:00:00Z",
    )
    write_to_mailbox(
        "alice", msg, team_name="t", workspace_root=tmp_path,
    )

    sweep_mailboxes(
        runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
        recipient_to_agent_id={"alice": agent_id},
    )

    state = reg.get(agent_id)
    assert state.pending_user_messages == ["hi alice"]


# ---------------------------------------------------------------------------
# shutdown_request envelope
# ---------------------------------------------------------------------------


def test_shutdown_request_sets_flag(tmp_path: Path) -> None:
    reg = RuntimeTaskRegistry()
    agent_id = _make_teammate(reg)

    _write_envelope(
        recipient="alice",
        envelope={
            "type": "shutdown_request",
            "request_id": "req-1",
            "from": "team-lead",
        },
        sender="team-lead",
        tmp_path=tmp_path,
    )

    sweep_mailboxes(
        runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
        recipient_to_agent_id={"alice": agent_id},
    )

    state = reg.get(agent_id)
    assert state.shutdown_requested is True


# ---------------------------------------------------------------------------
# plan_approval_response — receiver-side gate (concern C3 / D1)
# ---------------------------------------------------------------------------


def test_plan_approval_from_lead_clears_flag(tmp_path: Path) -> None:
    """Lead's envelope is honored: ``awaiting_plan_approval`` is
    cleared, ``permission_mode`` updated."""
    reg = RuntimeTaskRegistry()
    agent_id = _make_teammate(reg)
    reg.update(agent_id, lambda s: replace(
        s, awaiting_plan_approval=True, permission_mode="plan",
    ))

    _write_envelope(
        recipient="alice",
        envelope={
            "type": "plan_approval_response",
            "request_id": "req-1",
            "approved": True,
            "permission_mode": "default",
            "from": "lead-1",  # matches expected_lead_agent_id
        },
        sender="team-lead",
        tmp_path=tmp_path,
    )

    sweep_mailboxes(
        runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
        expected_lead_agent_id="lead-1",
        recipient_to_agent_id={"alice": agent_id},
    )

    state = reg.get(agent_id)
    assert state.awaiting_plan_approval is False
    assert state.permission_mode == "default"


def test_plan_approval_from_non_lead_logged_and_dropped(
    tmp_path: Path, caplog
) -> None:
    """Critic concern C3 + Chunk-F D1: a non-lead writing a
    ``plan_approval_response`` envelope MUST be log-and-drop. The
    teammate state stays unchanged; the poller doesn't crash."""
    reg = RuntimeTaskRegistry()
    agent_id = _make_teammate(reg)
    reg.update(agent_id, lambda s: replace(
        s, awaiting_plan_approval=True, permission_mode="plan",
    ))

    _write_envelope(
        recipient="alice",
        envelope={
            "type": "plan_approval_response",
            "request_id": "req-2",
            "approved": True,
            "permission_mode": "default",
            "from": "imposter-456",  # does NOT match
        },
        sender="imposter-456",
        tmp_path=tmp_path,
    )

    with caplog.at_level("WARNING"):
        sweep_mailboxes(
            runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
            expected_lead_agent_id="lead-1",
            recipient_to_agent_id={"alice": agent_id},
        )

    # Teammate state unchanged — the envelope was dropped.
    state = reg.get(agent_id)
    assert state.awaiting_plan_approval is True
    assert state.permission_mode == "plan"

    # Log-and-drop emitted a warning.
    warnings = [r for r in caplog.records if "doesn't match" in r.message]
    assert len(warnings) == 1


def test_plan_approval_skips_when_no_lead_id_configured(
    tmp_path: Path, caplog
) -> None:
    """If the poller has no ``expected_lead_agent_id`` (team file not
    loaded yet), it must NOT honor the envelope blindly — drop it."""
    reg = RuntimeTaskRegistry()
    agent_id = _make_teammate(reg)
    reg.update(agent_id, lambda s: replace(s, awaiting_plan_approval=True))

    _write_envelope(
        recipient="alice",
        envelope={
            "type": "plan_approval_response",
            "request_id": "req-3",
            "approved": True,
            "permission_mode": "default",
            "from": "anyone",
        },
        sender="anyone",
        tmp_path=tmp_path,
    )

    with caplog.at_level("WARNING"):
        sweep_mailboxes(
            runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
            expected_lead_agent_id=None,  # not yet known
            recipient_to_agent_id={"alice": agent_id},
        )

    state = reg.get(agent_id)
    assert state.awaiting_plan_approval is True


# ---------------------------------------------------------------------------
# permission_response → leader bridge
# ---------------------------------------------------------------------------


def test_permission_response_fires_callback(tmp_path: Path) -> None:
    fired: dict[str, str] = {}

    register_permission_callback(
        request_id="req-perm-1", tool_use_id="toolu_x",
        on_allow=lambda: fired.setdefault("decision", "allow"),
        on_reject=lambda reason: fired.setdefault("decision", f"reject:{reason}"),
    )

    reg = RuntimeTaskRegistry()
    agent_id = _make_teammate(reg)

    _write_envelope(
        recipient="alice",
        envelope={
            "type": "permission_response",
            "request_id": "req-perm-1",
            "approved": True,
        },
        sender="team-lead",
        tmp_path=tmp_path,
    )

    sweep_mailboxes(
        runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
        recipient_to_agent_id={"alice": agent_id},
    )

    assert fired == {"decision": "allow"}


# ---------------------------------------------------------------------------
# Read-offset cursor — no replay across sweeps
# ---------------------------------------------------------------------------


def test_offset_advances_so_envelopes_are_not_replayed(tmp_path: Path) -> None:
    reg = RuntimeTaskRegistry()
    agent_id = _make_teammate(reg)

    msg = TeammateMessage(from_="x", text="first", timestamp="t")
    write_to_mailbox("alice", msg, team_name="t", workspace_root=tmp_path)

    n1 = sweep_mailboxes(
        runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
        recipient_to_agent_id={"alice": agent_id},
    )
    assert n1 == 1
    state = reg.get(agent_id)
    assert state.pending_user_messages == ["first"]

    # Second sweep with no new messages → 0 dispatches.
    n2 = sweep_mailboxes(
        runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
        recipient_to_agent_id={"alice": agent_id},
    )
    assert n2 == 0
    state = reg.get(agent_id)
    assert state.pending_user_messages == ["first"]  # not duplicated


# ---------------------------------------------------------------------------
# Daemon thread lifecycle
# ---------------------------------------------------------------------------


def test_daemon_starts_and_stops_cleanly(tmp_path: Path) -> None:
    reg = RuntimeTaskRegistry()
    agent_id = _make_teammate(reg)

    msg = TeammateMessage(from_="x", text="async-delivered", timestamp="t")
    write_to_mailbox("alice", msg, team_name="t", workspace_root=tmp_path)

    start_mailbox_poller(
        runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
        recipient_to_agent_id_provider=lambda: {"alice": agent_id},
        tick_seconds=0.05,
    )

    deadline = time.time() + 1.0
    while time.time() < deadline:
        if reg.get(agent_id).pending_user_messages:
            break
        time.sleep(0.02)

    assert reg.get(agent_id).pending_user_messages == ["async-delivered"]

    stop_mailbox_poller(timeout=1.0)
    live = [t for t in threading.enumerate() if t.name == "mailbox-poller"]
    assert live == []


def test_daemon_start_is_idempotent(tmp_path: Path) -> None:
    reg = RuntimeTaskRegistry()
    start_mailbox_poller(
        runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
        recipient_to_agent_id_provider=lambda: {},
        tick_seconds=0.5,
    )
    start_mailbox_poller(
        runtime_tasks=reg, workspace_root=tmp_path, team_name="t",
        recipient_to_agent_id_provider=lambda: {},
        tick_seconds=0.5,
    )
    live = [t for t in threading.enumerate() if t.name == "mailbox-poller"]
    assert len(live) == 1
    stop_mailbox_poller(timeout=1.0)

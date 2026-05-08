"""WI-9.1 tests — leader permission bridge."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.services.swarm.leader_permission_bridge import (
    PermissionRequest,
    _reset_callbacks,
    create_permission_request,
    deliver_permission_decision,
    get_pending_request_ids,
    register_permission_callback,
    send_permission_request_via_mailbox,
    unregister_permission_callback,
)
from src.services.swarm.mailbox import read_mailbox


@pytest.fixture(autouse=True)
def _clear_callbacks() -> None:
    """Each test starts with no registered callbacks. Module-level
    state can otherwise leak across tests."""
    _reset_callbacks()
    yield
    _reset_callbacks()


# ---------------------------------------------------------------------------
# PermissionRequest dataclass
# ---------------------------------------------------------------------------


def test_create_permission_request_auto_generates_request_id() -> None:
    req = create_permission_request(
        tool_name="Bash", tool_use_id="toolu_1",
        input={"command": "echo hi"},
    )
    assert req.request_id.startswith("perm-")
    assert req.tool_name == "Bash"
    assert req.tool_use_id == "toolu_1"


def test_create_permission_request_uses_supplied_id() -> None:
    req = create_permission_request(
        tool_name="Bash", tool_use_id="toolu_1",
        input={"command": "echo hi"},
        request_id="req-fixed-1",
    )
    assert req.request_id == "req-fixed-1"


def test_to_envelope_shape() -> None:
    req = create_permission_request(
        tool_name="Bash", tool_use_id="toolu_1",
        input={"command": "echo hi"},
        description="user typed yes",
        permission_suggestions=["Always allow"],
    )
    env = req.to_envelope()
    assert env["type"] == "permission_request"
    assert env["tool_name"] == "Bash"
    assert env["input"] == {"command": "echo hi"}
    assert env["description"] == "user typed yes"
    assert env["permission_suggestions"] == ["Always allow"]
    assert "timestamp" in env


# ---------------------------------------------------------------------------
# Callback registry — register / dispatch / unregister
# ---------------------------------------------------------------------------


def test_register_and_dispatch_allow() -> None:
    fired: dict[str, Any] = {}

    register_permission_callback(
        request_id="req-1", tool_use_id="toolu_1",
        on_allow=lambda: fired.setdefault("allow", True),
        on_reject=lambda reason: fired.setdefault("reject", reason),
    )

    assert "req-1" in get_pending_request_ids()
    assert deliver_permission_decision("req-1", approved=True) is True
    assert fired == {"allow": True}
    assert "req-1" not in get_pending_request_ids()  # auto-unregistered


def test_register_and_dispatch_reject() -> None:
    fired: dict[str, Any] = {}

    register_permission_callback(
        request_id="req-2", tool_use_id="toolu_1",
        on_allow=lambda: fired.setdefault("allow", True),
        on_reject=lambda reason: fired.setdefault("reject", reason),
    )

    deliver_permission_decision("req-2", approved=False, reason="too risky")
    assert fired == {"reject": "too risky"}


def test_dispatch_unknown_request_id_returns_false() -> None:
    """A second decision for the same request (already fired) is a no-op."""
    register_permission_callback(
        request_id="req-3", tool_use_id="toolu_1",
        on_allow=lambda: None, on_reject=lambda reason: None,
    )
    deliver_permission_decision("req-3", approved=True)  # consumes
    assert deliver_permission_decision("req-3", approved=True) is False


def test_unregister_callback_returns_true_on_hit() -> None:
    register_permission_callback(
        request_id="req-4", tool_use_id="toolu_1",
        on_allow=lambda: None, on_reject=lambda reason: None,
    )
    assert unregister_permission_callback("req-4") is True
    assert unregister_permission_callback("req-4") is False


def test_callback_exception_does_not_break_dispatch() -> None:
    """A misbehaving callback shouldn't crash the dispatcher;
    ``deliver_permission_decision`` returns True (the callback fired)
    but logs the exception."""
    register_permission_callback(
        request_id="req-5", tool_use_id="toolu_1",
        on_allow=lambda: 1 / 0,  # raises ZeroDivisionError
        on_reject=lambda reason: None,
    )
    assert deliver_permission_decision("req-5", approved=True) is True


# ---------------------------------------------------------------------------
# Mailbox transport
# ---------------------------------------------------------------------------


def test_send_permission_request_writes_envelope_to_leader_mailbox(
    tmp_path: Path,
) -> None:
    req = create_permission_request(
        tool_name="Bash", tool_use_id="toolu_xyz",
        input={"command": "rm -rf /"},
        description="dangerous",
    )
    asyncio.run(
        send_permission_request_via_mailbox(
            req,
            leader_name="team-lead",
            sender_name="researcher",
            team_name="t",
            workspace_root=tmp_path,
        )
    )

    msgs = read_mailbox("team-lead", team_name="t", workspace_root=tmp_path)
    assert len(msgs) == 1
    envelope = json.loads(msgs[0].text)
    assert envelope["type"] == "permission_request"
    assert envelope["tool_name"] == "Bash"
    assert envelope["request_id"] == req.request_id
    assert envelope["description"] == "dangerous"
    assert msgs[0].from_ == "researcher"

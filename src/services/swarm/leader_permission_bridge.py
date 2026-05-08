"""Leader permission bridge — Chunk H / WI-9.1.

Mirrors ``typescript/src/utils/swarm/leaderPermissionBridge.ts`` (per
gap analysis #15). Workers escalate permission requests to the leader
via the team mailbox; the leader's UI surfaces the request, approves
or rejects, and the worker's registered callback fires with the
decision.

The chapter §"Permission Forwarding" calls this out as the swarm
counterpart to single-agent permission prompting: workers operate
autonomously for safe tools (covered by the bash classifier's
auto-approval) but escalate dangerous operations through the
leader's review.

Relationship to ``services/swarm/permissions.py``
-------------------------------------------------

Different concept, same package. ``permissions.py``'s
``SwarmPermissionSync`` is a *decision cache*: when the user approves
``Bash(echo hi)`` for one teammate, that decision propagates to all
other teammates so they don't re-prompt. This module is *request
forwarding*: when a worker hits a tool that needs approval, the
request is forwarded to the leader.

The two cooperate — the bash classifier consults
``SwarmPermissionSync`` first; on miss, the request is forwarded via
this bridge.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from src.services.swarm.mailbox import (
    TeammateMessage,
    make_iso_timestamp,
    write_to_mailbox,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Permission-request envelope shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionRequest:
    """One in-flight permission request from a worker to the leader.

    Mirrors TS ``createPermissionRequest`` payload at
    ``leaderPermissionBridge.ts``. The dataclass is frozen so request
    identity (``request_id``) can't change after creation; the
    decision is delivered through the registered callbacks
    (``on_allow`` / ``on_reject``) rather than mutation.

    ``permission_suggestions`` is an optional list of per-tool hints
    the leader's UI can surface (e.g., "Allow this command for the
    rest of the session"). Empty list when no suggestions apply.
    """

    request_id: str
    tool_name: str
    tool_use_id: str
    input: dict[str, Any]
    description: str | None = None
    permission_suggestions: list[str] = field(default_factory=list)

    def to_envelope(self) -> dict[str, Any]:
        """Serialize for transport via the team mailbox."""
        return {
            "type": "permission_request",
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "tool_use_id": self.tool_use_id,
            "input": self.input,
            "description": self.description,
            "permission_suggestions": list(self.permission_suggestions),
            "timestamp": make_iso_timestamp(),
        }


# ---------------------------------------------------------------------------
# Callback registry — keyed by request_id
# ---------------------------------------------------------------------------


@dataclass
class _CallbackEntry:
    """Internal record — pairs a request_id with its (on_allow, on_reject)
    callbacks plus the tool_use_id for diagnostics."""
    request_id: str
    tool_use_id: str
    on_allow: Callable[[], None]
    on_reject: Callable[[str], None]
    registered_at: float = field(default_factory=time.time)


# Module-level registry — keyed by request_id. Guarded by an RLock so
# the mailbox poller (a separate thread) can dispatch decisions while
# workers register new callbacks.
_callback_lock = threading.RLock()
_callbacks: dict[str, _CallbackEntry] = {}


def create_permission_request(
    *,
    tool_name: str,
    tool_use_id: str,
    input: dict[str, Any],
    description: str | None = None,
    permission_suggestions: list[str] | None = None,
    request_id: str | None = None,
) -> PermissionRequest:
    """Build a fresh ``PermissionRequest`` with a CSPRNG-backed id.

    ``request_id`` is auto-generated when omitted (the common path).
    Tests / replay scenarios can pass a fixed id for deterministic
    behavior.
    """
    rid = request_id or f"perm-{uuid.uuid4().hex[:12]}"
    return PermissionRequest(
        request_id=rid,
        tool_name=tool_name,
        tool_use_id=tool_use_id,
        input=dict(input),
        description=description,
        permission_suggestions=list(permission_suggestions or []),
    )


def register_permission_callback(
    *,
    request_id: str,
    tool_use_id: str,
    on_allow: Callable[[], None],
    on_reject: Callable[[str], None],
) -> None:
    """Register the callbacks the mailbox poller will fire when the
    leader's decision arrives.

    Idempotent on ``request_id`` — re-registering replaces. Callers
    should call ``unregister_permission_callback`` when the request
    times out or is otherwise abandoned.
    """
    with _callback_lock:
        _callbacks[request_id] = _CallbackEntry(
            request_id=request_id,
            tool_use_id=tool_use_id,
            on_allow=on_allow,
            on_reject=on_reject,
        )


def unregister_permission_callback(request_id: str) -> bool:
    """Drop a callback registration. Returns True if a registration
    existed."""
    with _callback_lock:
        return _callbacks.pop(request_id, None) is not None


def get_pending_request_ids() -> list[str]:
    """Snapshot of currently-registered request ids. Test/diagnostic
    use; production code dispatches via ``deliver_permission_decision``."""
    with _callback_lock:
        return list(_callbacks.keys())


def deliver_permission_decision(
    request_id: str,
    *,
    approved: bool,
    reason: str | None = None,
) -> bool:
    """Fire the registered callback for ``request_id``.

    Returns True iff a callback was registered and fired. The
    callback is unregistered after firing so a retry / duplicate
    decision is a no-op.

    Per A6/C5: the registry's RLock is held only for the pop; the
    callback fires AFTER the lock is released, so callbacks may
    safely re-enter the registry (e.g. to register a follow-up
    request, look up another callback, etc.) without deadlocking.
    Async work inside callbacks is still discouraged — fire-and-
    forget via ``asyncio.create_task`` rather than ``await``ing —
    so the dispatcher's caller doesn't observe surprising blocking
    behavior, but it's a contract preference, not a deadlock
    requirement.
    """
    with _callback_lock:
        entry = _callbacks.pop(request_id, None)
    if entry is None:
        return False
    try:
        if approved:
            entry.on_allow()
        else:
            entry.on_reject(reason or "")
    except Exception:
        logger.exception(
            "permission decision callback raised for request_id=%s tool_use_id=%s",
            request_id, entry.tool_use_id,
        )
    return True


# ---------------------------------------------------------------------------
# Mailbox transport
# ---------------------------------------------------------------------------


async def send_permission_request_via_mailbox(
    request: PermissionRequest,
    *,
    leader_name: str,
    sender_name: str,
    team_name: str,
    workspace_root: Path,
) -> None:
    """Forward ``request`` to the leader's mailbox.

    Mirrors ``sendPermissionRequestViaMailbox`` from
    ``leaderPermissionBridge.ts``. The envelope is serialized into
    ``TeammateMessage.text`` (JSON-encoded dict) so the mailbox
    poller can recognize and dispatch it without bespoke parsing.

    Synchronous file IO — no event loop required even though the
    function is ``async def``. The signature matches the chapter's
    typical async-mailbox shape so future variants (UDS / bridge)
    fit without retyping.
    """
    import json

    envelope = request.to_envelope()
    msg = TeammateMessage(
        from_=sender_name,
        text=json.dumps(envelope, ensure_ascii=False),
        timestamp=envelope["timestamp"],
    )
    write_to_mailbox(
        leader_name, msg, team_name=team_name, workspace_root=workspace_root,
    )


# ---------------------------------------------------------------------------
# Test-friendly reset
# ---------------------------------------------------------------------------


def _reset_callbacks() -> None:
    """Clear all registered callbacks. Test-only helper — production
    code should never need this; the natural lifecycle (register on
    request, unregister on decision) handles cleanup."""
    with _callback_lock:
        _callbacks.clear()


__all__ = [
    "PermissionRequest",
    "create_permission_request",
    "register_permission_callback",
    "unregister_permission_callback",
    "get_pending_request_ids",
    "deliver_permission_decision",
    "send_permission_request_via_mailbox",
]

"""Mailbox poller daemon — Chunk H / WI-9.1 + plan-mode receiver-side
defense-in-depth (deferred from Chunk-F D1).

Periodically reads each tracked recipient's inbox file and dispatches
structured envelopes back into runtime state:

* ``shutdown_request`` → set ``shutdown_requested=True`` on the
  recipient's ``InProcessTeammateTaskState``. The teammate's run
  loop checks the flag at natural stopping points and winds down
  cooperatively.
* ``shutdown_response`` → fire the teammate's shutdown callback
  (deferred — Phase 9 doesn't wire a callback registry; flagged for
  the run-loop integration ticket).
* ``plan_approval_response`` → **verify envelope ``from`` claims
  ``lead_agent_id``** (chapter §"Plan-mode lifecycle" + critic
  concern C3 from refactoring-plan review + Chunk-F D1 deferral).
  On match: clear ``awaiting_plan_approval``, set
  ``permission_mode``. On mismatch: log-and-drop (non-leader trying
  to forge an approval is a real attack vector — drop silently
  rather than crash).
* ``permission_request`` → routed to the leader bridge's
  ``deliver_permission_decision`` upstream when the leader UI
  surfaces the decision; this poller passes the request on to the
  registry of the agent that's running on the leader's side. (See
  ``leader_permission_bridge.py``.)

Cursor file
-----------

Per inbox file we track a read offset in
``<inbox_path>.read_offset``. The poller advances the offset after
each successful dispatch. Restarts pick up where they left off; new
messages aren't replayed.

Threading
---------

Daemon thread, started lazily on first ``start_mailbox_poller`` call,
joinable via ``stop_mailbox_poller``. Tick rate ~1s by default
(faster than eviction's 5s — permission-request latency matters
more than eviction lag for swarm UX).

The poller is a SEPARATE daemon from the eviction sweeper (per the
brief — SRP-clean: eviction = cleanup, mailbox = inbound dispatch).
Both are short-lived ticks over ``runtime_tasks``; co-locating their
threads as a future optimization is fine but not in this chunk.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from src.services.swarm.leader_permission_bridge import deliver_permission_decision
from src.services.swarm.mailbox import get_inbox_path, read_mailbox

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry

logger = logging.getLogger(__name__)


_POLLER_TICK_SECONDS: float = 1.0


def _read_offset_path(inbox_path: Path) -> Path:
    return inbox_path.with_suffix(inbox_path.suffix + ".read_offset")


def _read_offset(inbox_path: Path) -> int:
    """Return the message-index cursor for ``inbox_path``. 0 if absent
    (fresh inbox or first-ever poll)."""
    cursor = _read_offset_path(inbox_path)
    if not cursor.exists():
        return 0
    try:
        return int(cursor.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_offset(inbox_path: Path, offset: int) -> None:
    cursor = _read_offset_path(inbox_path)
    try:
        cursor.write_text(str(offset), encoding="utf-8")
    except OSError:
        logger.exception("failed to update mailbox read-offset for %s", inbox_path)


def _try_parse_envelope(text: str) -> dict[str, Any] | None:
    """Try to interpret ``message.text`` as a JSON-encoded structured
    envelope (the SendMessage tool serializes structured-protocol
    payloads this way). Returns ``None`` for plain-text messages."""
    if not text or not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and "type" in parsed:
        return parsed
    return None


# ---------------------------------------------------------------------------
# Envelope dispatchers — one per envelope type
# ---------------------------------------------------------------------------


def _dispatch_shutdown_request(
    *,
    envelope: dict[str, Any],
    teammate_agent_id: str,
    runtime_tasks: "RuntimeTaskRegistry",
) -> None:
    """Set ``shutdown_requested=True`` on the recipient's teammate
    state. Cooperative termination — the teammate winds down at a
    natural stopping point."""
    from src.tasks.in_process_teammate import InProcessTeammateTaskState

    def _set(prev: Any) -> Any:
        if not isinstance(prev, InProcessTeammateTaskState):
            return prev
        if prev.shutdown_requested:
            return prev
        return replace(prev, shutdown_requested=True)

    runtime_tasks.update(teammate_agent_id, _set)


def _dispatch_plan_approval_response(
    *,
    envelope: dict[str, Any],
    teammate_agent_id: str,
    runtime_tasks: "RuntimeTaskRegistry",
    expected_lead_agent_id: str | None,
) -> None:
    """Critic concern C3 + Chunk-F D1: verify ``envelope["from"]`` is
    the expected ``lead_agent_id`` BEFORE flipping teammate state.

    A non-leader writing this envelope is a real attack vector
    (someone bypassing the SendMessage sender-side gate via direct
    mailbox write). The defense-in-depth: log-and-drop. Don't raise
    — that would crash the poller for the rest of the queue and
    let one bad envelope take out other recipients' inboxes.
    """
    from src.tasks.in_process_teammate import InProcessTeammateTaskState

    sender = envelope.get("from")
    if not expected_lead_agent_id or sender != expected_lead_agent_id:
        logger.warning(
            "plan_approval_response from %r doesn't match team's "
            "lead_agent_id %r — dropping envelope (request_id=%r). "
            "This is the receiver-side defense-in-depth gate; the "
            "sender-side gate (is_team_lead) should have caught this "
            "first. If you see this in production, investigate the "
            "non-SendMessage write path.",
            sender, expected_lead_agent_id,
            envelope.get("request_id"),
        )
        return

    approved = bool(envelope.get("approved"))
    permission_mode = envelope.get("permission_mode")

    def _apply(prev: Any) -> Any:
        if not isinstance(prev, InProcessTeammateTaskState):
            return prev
        new_permission_mode = (
            permission_mode if isinstance(permission_mode, str)
            else prev.permission_mode
        )
        return replace(
            prev,
            awaiting_plan_approval=False,
            permission_mode=new_permission_mode,
        )

    runtime_tasks.update(teammate_agent_id, _apply)
    logger.info(
        "plan_approval_response: teammate=%s approved=%s permission_mode=%s",
        teammate_agent_id, approved, permission_mode,
    )


def _dispatch_permission_response(envelope: dict[str, Any]) -> None:
    """Forward a permission decision to ``leader_permission_bridge``.

    The leader's UI ultimately calls ``deliver_permission_decision``
    once it has the user's answer; if the decision arrives via
    mailbox first (e.g. a remote leader), the poller picks it up
    and dispatches.
    """
    request_id = envelope.get("request_id")
    if not isinstance(request_id, str):
        return
    approved = bool(envelope.get("approved"))
    reason = envelope.get("reason")
    deliver_permission_decision(
        request_id, approved=approved,
        reason=reason if isinstance(reason, str) else None,
    )


# ---------------------------------------------------------------------------
# Single sweep — read each tracked inbox, dispatch new envelopes
# ---------------------------------------------------------------------------


def sweep_mailboxes(
    *,
    runtime_tasks: "RuntimeTaskRegistry",
    workspace_root: Path,
    team_name: str,
    expected_lead_agent_id: str | None = None,
    recipient_to_agent_id: dict[str, str] | None = None,
) -> int:
    """Read every tracked recipient's inbox and dispatch new envelopes.

    Returns the count of envelopes dispatched on this sweep (for
    observability / test assertions).

    ``recipient_to_agent_id`` maps the on-disk recipient name to the
    in-process teammate's ``agent_id``. Callers that don't have this
    mapping populated yet (e.g. a fresh team with no in-process
    teammates) pass ``None`` and only the permission-response branch
    fires (those go to the bridge by request_id, not teammate id).
    """
    if recipient_to_agent_id is None:
        recipient_to_agent_id = {}

    dispatched = 0

    for recipient_name, agent_id in list(recipient_to_agent_id.items()):
        try:
            inbox_path = get_inbox_path(recipient_name, team_name, workspace_root)
        except ValueError:
            # Sanitization-rejected name — skip; the inbox can't exist.
            continue
        if not inbox_path.exists():
            continue

        all_msgs = read_mailbox(
            recipient_name, team_name=team_name, workspace_root=workspace_root,
        )
        offset = _read_offset(inbox_path)
        new_msgs = all_msgs[offset:]
        if not new_msgs:
            continue

        for msg in new_msgs:
            envelope = _try_parse_envelope(msg.text)
            if envelope is None:
                # Plain-text message — surface to the teammate's
                # ``pending_user_messages`` queue so the run loop
                # picks it up at the next tool round.
                _enqueue_user_message(
                    runtime_tasks, agent_id, msg.text,
                )
                dispatched += 1
                continue

            envelope_type = envelope.get("type")
            if envelope_type == "shutdown_request":
                _dispatch_shutdown_request(
                    envelope=envelope, teammate_agent_id=agent_id,
                    runtime_tasks=runtime_tasks,
                )
                dispatched += 1
            elif envelope_type == "plan_approval_response":
                _dispatch_plan_approval_response(
                    envelope=envelope, teammate_agent_id=agent_id,
                    runtime_tasks=runtime_tasks,
                    expected_lead_agent_id=expected_lead_agent_id,
                )
                dispatched += 1
            elif envelope_type == "permission_response":
                _dispatch_permission_response(envelope)
                dispatched += 1
            elif envelope_type == "shutdown_response":
                # Surface to a logger — the cooperative-shutdown
                # callback registry isn't part of this chunk; the
                # leader's UI consumes shutdown_response separately.
                logger.info(
                    "shutdown_response received for request_id=%r; "
                    "callback dispatch is wired in the run-loop "
                    "integration ticket.",
                    envelope.get("request_id"),
                )
                dispatched += 1
            else:
                logger.warning(
                    "unknown envelope type %r in mailbox %s — dropping.",
                    envelope_type, inbox_path,
                )

        _write_offset(inbox_path, offset + len(new_msgs))

    return dispatched


def _enqueue_user_message(
    runtime_tasks: "RuntimeTaskRegistry",
    agent_id: str,
    text: str,
) -> None:
    """Append a plain-text message to the teammate's
    ``pending_user_messages`` queue."""
    from src.tasks.in_process_teammate import InProcessTeammateTaskState

    def _append(prev: Any) -> Any:
        if not isinstance(prev, InProcessTeammateTaskState):
            return prev
        return replace(
            prev,
            pending_user_messages=[*prev.pending_user_messages, text],
        )

    runtime_tasks.update(agent_id, _append)


# ---------------------------------------------------------------------------
# Daemon thread lifecycle
# ---------------------------------------------------------------------------


_poller_lock = threading.Lock()
_poller_thread: threading.Thread | None = None
_poller_stop = threading.Event()


def start_mailbox_poller(
    *,
    runtime_tasks: "RuntimeTaskRegistry",
    workspace_root: Path,
    team_name: str,
    expected_lead_agent_id: str | None = None,
    recipient_to_agent_id_provider: Any = None,
    tick_seconds: float = _POLLER_TICK_SECONDS,
) -> None:
    """Start the daemon. Idempotent — re-calling is a no-op while
    the poller is already running.

    ``recipient_to_agent_id_provider`` is a callable that returns the
    current name → agent_id map at sweep time. Using a provider
    rather than a static dict lets the team roster grow / shrink
    between sweeps without poller restart.
    """
    global _poller_thread
    with _poller_lock:
        if _poller_thread is not None and _poller_thread.is_alive():
            return
        _poller_stop.clear()
        thread = threading.Thread(
            target=_poller_loop,
            args=(
                runtime_tasks, workspace_root, team_name,
                expected_lead_agent_id,
                recipient_to_agent_id_provider,
                tick_seconds,
                _poller_stop,
            ),
            name="mailbox-poller",
            daemon=True,
        )
        _poller_thread = thread
        thread.start()


def stop_mailbox_poller(*, timeout: float = 2.0) -> None:
    """Signal the poller to exit and wait for it. Safe to call when
    no poller is running."""
    global _poller_thread
    with _poller_lock:
        if _poller_thread is None:
            return
        thread = _poller_thread
        _poller_stop.set()
        _poller_thread = None
    thread.join(timeout=timeout)


def _poller_loop(
    runtime_tasks: "RuntimeTaskRegistry",
    workspace_root: Path,
    team_name: str,
    expected_lead_agent_id: str | None,
    recipient_to_agent_id_provider: Any,
    tick_seconds: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        if stop_event.wait(timeout=tick_seconds):
            return
        try:
            mapping = (
                recipient_to_agent_id_provider()
                if callable(recipient_to_agent_id_provider)
                else recipient_to_agent_id_provider
            )
            sweep_mailboxes(
                runtime_tasks=runtime_tasks,
                workspace_root=workspace_root,
                team_name=team_name,
                expected_lead_agent_id=expected_lead_agent_id,
                recipient_to_agent_id=mapping,
            )
        except Exception:
            logger.exception("mailbox poller iteration failed")


__all__ = [
    "sweep_mailboxes",
    "start_mailbox_poller",
    "stop_mailbox_poller",
]

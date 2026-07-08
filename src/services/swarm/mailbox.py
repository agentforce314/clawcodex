"""File-based JSONL mailbox — Chunk F / WI-6.3.

Mirrors the ``writeToMailbox`` / envelope-helper surface in
``typescript/src/utils/teammateMailbox.ts``. Per assumption A4
(JSONL with atomic-append-without-locking) and critic concern C1
(path-traversal sanitization on model-controlled names).

Format
------

JSONL — one JSON object per line, UTF-8, terminated with ``\\n``.
Reader is tolerant of trailing partial writes (writer-crashed-mid-
write); see ``read_unread_messages``.

Storage
-------

* Per-team directory: ``<workspace_root>/.clawcodex/mailboxes/<team>/``
* Per-recipient file: ``<recipient>.jsonl``
* Both names sanitized against ``[A-Za-z0-9_-]{1,64}`` whitelist
  BEFORE any filesystem call. ``ValueError`` on rejection — no
  silent escape, no fallback.

Concurrency
-----------

Writes go through ``os.write`` on an ``O_APPEND`` file descriptor.
POSIX guarantees atomic appends for sub-PIPE_BUF (≥4 KiB) writes;
mailbox messages are well under that. Multi-writer interleaving at
sub-PIPE_BUF granularity is not possible. Lines >4 KiB *can*
interleave under heavy concurrency; the reader's tolerant parser
absorbs the resulting partial line.

Out of scope (per critic concern C4 / Phase 11)
-----------------------------------------------

GC / rotation / age-based eviction. Mailbox files grow unbounded
under this WI; a separate ticket lands the cleanup policy.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path sanitization (critic concern C1)
# ---------------------------------------------------------------------------

# Whitelist allowing alphanumerics, underscore, and dash. ``..`` and
# ``/`` are rejected — both would let a malicious or buggy ``to:``
# field write outside the mailboxes dir.
_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _sanitize_name(name: str, *, kind: str) -> str:
    """Reject any name that could escape the mailboxes directory.

    Whitelist rather than blocklist — explicit safer than clever. Names
    failing the check raise ``ValueError`` BEFORE any filesystem
    operation runs (no path resolved, no file opened).

    Per critic concern C1: ``recipient_name`` and ``team_name`` reach
    this function from model-controlled inputs (SendMessage's ``to:``
    field, TeamCreate's ``team_name``). The whitelist closes the
    traversal vector.
    """
    if not isinstance(name, str) or not _VALID_NAME_RE.match(name):
        raise ValueError(
            f"Invalid {kind}: {name!r} "
            "(allowed chars: A-Z, a-z, 0-9, _, -; max 64; min 1)."
        )
    return name


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_mailboxes_root(workspace_root: Path, team_name: str) -> Path:
    """Return ``<workspace_root>/.clawcodex/mailboxes/<team>/`` (created
    if absent). ``team_name`` is sanitized before path composition."""
    safe_team = _sanitize_name(team_name, kind="team_name")
    root = workspace_root / ".clawcodex" / "mailboxes" / safe_team
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_inbox_path(
    recipient_name: str, team_name: str, workspace_root: Path
) -> Path:
    """Return absolute path to ``<recipient>.jsonl`` for the given team.

    Both names are sanitized via ``_sanitize_name``; either failing
    the whitelist raises ``ValueError`` BEFORE any filesystem call.
    """
    safe_recipient = _sanitize_name(recipient_name, kind="recipient_name")
    return get_mailboxes_root(workspace_root, team_name) / f"{safe_recipient}.jsonl"


# ---------------------------------------------------------------------------
# Envelope dataclass — chapter-canonical message shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeammateMessage:
    """One mailbox entry. Mirrors TS ``teammateMailbox.ts:TeammateMessage``."""

    from_: str
    text: str
    timestamp: str  # ISO 8601
    summary: str | None = None
    color: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        """Serialize for disk. ``from_`` → ``from`` to match TS shape."""
        out: dict[str, Any] = {
            "from": self.from_,
            "text": self.text,
            "timestamp": self.timestamp,
        }
        if self.summary is not None:
            out["summary"] = self.summary
        if self.color is not None:
            out["color"] = self.color
        return out

    @classmethod
    def from_jsonable(cls, raw: dict[str, Any]) -> "TeammateMessage":
        return cls(
            from_=str(raw.get("from", "")),
            text=str(raw.get("text", "")),
            timestamp=str(raw.get("timestamp", "")),
            summary=raw.get("summary"),
            color=raw.get("color"),
        )


# ---------------------------------------------------------------------------
# Write — atomic O_APPEND
# ---------------------------------------------------------------------------


def write_to_mailbox(
    recipient_name: str,
    message: TeammateMessage,
    *,
    team_name: str,
    workspace_root: Path,
) -> None:
    """Append ``message`` to ``<recipient>.jsonl`` as one UTF-8 JSON line.

    Atomic at line granularity (single ``os.write`` of the encoded
    line on an ``O_APPEND`` fd). Synchronous — callers must NOT hold
    the runtime_tasks RLock across this call (A6/C5 contract).
    """
    path = get_inbox_path(recipient_name, team_name, workspace_root)
    line = json.dumps(message.to_jsonable(), ensure_ascii=False, separators=(",", ":")) + "\n"
    encoded = line.encode("utf-8")

    # ``O_APPEND`` makes every write atomic at the file-position level.
    # ``O_CLOEXEC`` keeps the fd from leaking to bash subprocesses.
    # ``0o600`` because mailboxes can carry sensitive plan content —
    # readable by the user only. ``O_CLOEXEC`` is POSIX-only; on Windows
    # it is absent (and fds are non-inheritable by default since PEP 446),
    # so fall back to 0.
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(f"mailbox write returned {written} for {path}")
            view = view[written:]
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Read — tolerant of partial trailing lines
# ---------------------------------------------------------------------------


def read_mailbox(
    recipient_name: str,
    *,
    team_name: str,
    workspace_root: Path,
) -> list[TeammateMessage]:
    """Read every parseable message; skip blank/unparseable lines.

    Returns an empty list if the file doesn't exist (a recipient with
    no inbox file). Logs a single warning if any unparseable line is
    encountered (writer-crashed-mid-write tolerance, mirroring the
    transcript reader's behavior).

    The ``log-once`` guard prevents a corrupt file from spamming the
    log on every read.
    """
    path = get_inbox_path(recipient_name, team_name, workspace_root)
    if not path.exists():
        return []

    messages: list[TeammateMessage] = []
    logged_partial = False
    try:
        handle = open(path, "rb")
    except OSError:
        logger.exception("mailbox open failed for %s", path)
        return []
    try:
        for raw_line in handle:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                if not logged_partial:
                    logger.warning(
                        "skipping unparseable mailbox line in %s "
                        "(file may have a trailing partial-write)",
                        path,
                    )
                    logged_partial = True
                continue
            if not isinstance(parsed, dict):
                continue
            messages.append(TeammateMessage.from_jsonable(parsed))
    finally:
        handle.close()
    return messages


# ---------------------------------------------------------------------------
# Envelope helpers — structured-protocol payloads
# ---------------------------------------------------------------------------


def make_iso_timestamp() -> str:
    """Produce an ISO-8601 timestamp suitable for ``TeammateMessage.timestamp``."""
    # ``time.time`` → ISO-8601 UTC with microsecond precision and a
    # ``Z`` suffix. Avoids the timezone-aware datetime overhead.
    import datetime as _dt
    return _dt.datetime.fromtimestamp(time.time(), tz=_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def create_shutdown_request_message(
    *, request_id: str, from_: str, reason: str | None = None
) -> dict[str, Any]:
    """Mirror ``createShutdownRequestMessage`` from teammateMailbox.ts."""
    out: dict[str, Any] = {
        "type": "shutdown_request",
        "request_id": request_id,
        "from": from_,
        "timestamp": make_iso_timestamp(),
    }
    if reason is not None:
        out["reason"] = reason
    return out


def create_shutdown_approved_message(
    *, request_id: str, from_: str
) -> dict[str, Any]:
    return {
        "type": "shutdown_response",
        "request_id": request_id,
        "from": from_,
        "approved": True,
        "timestamp": make_iso_timestamp(),
    }


def create_shutdown_rejected_message(
    *, request_id: str, from_: str, reason: str
) -> dict[str, Any]:
    return {
        "type": "shutdown_response",
        "request_id": request_id,
        "from": from_,
        "approved": False,
        "reason": reason,
        "timestamp": make_iso_timestamp(),
    }


def create_plan_approval_response_message(
    *,
    request_id: str,
    approved: bool,
    permission_mode: str,
    from_: str,
    feedback: str | None = None,
) -> dict[str, Any]:
    """Plan-approval response envelope (chapter §"Plan-mode lifecycle")."""
    out: dict[str, Any] = {
        "type": "plan_approval_response",
        "request_id": request_id,
        "approved": approved,
        "permission_mode": permission_mode,
        "from": from_,
        "timestamp": make_iso_timestamp(),
    }
    if feedback is not None:
        out["feedback"] = feedback
    return out


__all__ = [
    "TeammateMessage",
    "get_mailboxes_root",
    "get_inbox_path",
    "write_to_mailbox",
    "read_mailbox",
    "make_iso_timestamp",
    "create_shutdown_request_message",
    "create_shutdown_approved_message",
    "create_shutdown_rejected_message",
    "create_plan_approval_response_message",
]

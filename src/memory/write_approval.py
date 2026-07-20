"""Write-approval gate + pending store for memory writes.

Port of ``reference_projects/hermes-agent/tools/write_approval.py``,
memory-subsystem only (clawcodex has no skill write engine yet).

The bounded stores survive across sessions and are written from two
origins: **foreground** (a normal agent turn) and **background_review**
(the self-improvement fork that autonomously decides what to save — the
donor's source of the "wrong assumptions" users complained about). The
gate lets the user require review before anything is committed:

* ``memory_write_approval: false`` (default) — writes flow freely.
* ``true`` — no write is committed directly; the exact payload is
  **staged** to ``<user config dir>/pending/memory/<id>.json`` and
  surfaced for approval (``/memory pending|approve|reject``).

Deviation from the donor: hermes prompts inline for foreground writes on
its interactive CLI. clawcodex's only interactive surface is the TUI
behind the agent-server — a gateway-shaped channel with no inline prompt
path in this port — so gate-on always stages (exactly hermes' own gateway
behavior). The gate only ever *delays* a write, never drops it.

Pending records carry the exact replay payload, a summary, the write
origin (audit), and created_at; they survive restarts. Approved replays
go through :func:`apply_memory_pending`, which bypasses the gate but
re-runs the real store methods — and hence the threat scans and budget
checks.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from src.utils.clawcodex_dirs import get_user_config_dir

from .provenance import get_current_write_origin
from .store import MemoryStore

logger = logging.getLogger(__name__)

#: The single gated subsystem in this port.
MEMORY = "memory"


def write_approval_enabled() -> bool:
    """Whether the memory write gate is on (``memory_write_approval``
    setting). Defaults False (gate off) on any unset/invalid value so
    existing installs keep their behavior until the user opts in."""
    try:
        from src.settings.settings import get_settings

        return bool(getattr(get_settings(), "memory_write_approval", False))
    except Exception:  # noqa: BLE001 — a settings failure must not block writes
        return False


# ── pending store (file-backed) ───────────────────────────────────────


def _pending_dir() -> Path:
    return get_user_config_dir() / "pending" / MEMORY


def stage_write(payload: dict[str, Any], *, summary: str, origin: str | None = None) -> dict[str, Any]:
    """Persist a pending write and return its record.

    ``payload`` is the exact kwargs needed to replay the write on approval
    (``{"action": "add", "target": "user", "content": "..."}`` or the
    batch shape). Best-effort: on disk failure it logs and still returns a
    record — the write is simply lost, which is the safe failure for an
    approval gate (nothing is silently committed).
    """
    pid = uuid.uuid4().hex[:8]
    record = {
        "id": pid,
        "subsystem": MEMORY,
        "action": payload.get("action", ""),
        "summary": (summary or "").strip(),
        "origin": origin or get_current_write_origin(),
        "created_at": time.time(),
        "payload": payload,
    }
    try:
        d = _pending_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{pid}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:  # pragma: no cover - disk failure path
        logger.error("Failed to stage pending memory write: %s", e, exc_info=True)
    return record


def list_pending() -> list[dict[str, Any]]:
    """All pending memory-write records, oldest first."""
    d = _pending_dir()
    if not d.exists():
        return []
    records: list[dict[str, Any]] = []
    for p in d.glob("*.json"):
        try:
            records.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            logger.warning("Skipping unreadable pending record: %s", p)
    records.sort(key=lambda r: r.get("created_at", 0))
    return records


def get_pending(pending_id: str) -> dict[str, Any] | None:
    """A single pending record by id, or None."""
    path = _pending_dir() / f"{pending_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def discard_pending(pending_id: str) -> bool:
    """Delete a pending record. Returns True if it existed."""
    path = _pending_dir() / f"{pending_id}.json"
    try:
        if path.exists():
            path.unlink()
            return True
    except Exception as e:  # pragma: no cover
        logger.error("Failed to discard pending memory/%s: %s", pending_id, e)
    return False


def pending_count() -> int:
    """Cheap count of pending records (for badges/status lines)."""
    d = _pending_dir()
    if not d.exists():
        return 0
    try:
        return sum(1 for _ in d.glob("*.json"))
    except Exception:  # noqa: BLE001
        return 0


# ── approval replay ───────────────────────────────────────────────────


def apply_memory_pending(payload: dict[str, Any], store: MemoryStore) -> dict[str, Any]:
    """Replay a staged write against the store, bypassing the gate.

    Runs the real store methods, so the threat scans and budget checks are
    re-applied at approve time. Returns the store's result dict.
    """
    action = payload.get("action")
    target = payload.get("target", "memory")
    content = payload.get("content") or ""
    old_text = payload.get("old_text") or ""
    if action == "batch":
        return store.apply_batch(target, payload.get("operations") or [])
    if action == "add":
        return store.add(target, content)
    if action == "replace":
        return store.replace(target, old_text, content)
    if action == "remove":
        return store.remove(target, old_text)
    return {"success": False, "error": f"Unknown staged action '{action}'."}


__all__ = [
    "MEMORY",
    "apply_memory_pending",
    "discard_pending",
    "get_pending",
    "list_pending",
    "pending_count",
    "stage_write",
    "write_approval_enabled",
]

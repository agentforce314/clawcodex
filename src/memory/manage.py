"""``/memory`` subcommand surface for the bounded store — status,
pending-write review, approve / reject.

Pure text-in/text-out so every surface (agent-server ``memory_manage``
control, headless, tests) shares one implementation — the port of the
donor's ``/memory pending|approve|reject`` slash handlers
(``hermes_cli`` / ``gateway/slash_commands.py``; doc 07 §1).

The no-argument ``/memory`` stays the existing file picker (memdir /
CLAWCODEX.md editing); these subcommands only run when arguments are given.
"""

from __future__ import annotations

import logging

from . import write_approval as wa
from .store import get_memory_store

logger = logging.getLogger(__name__)

_USAGE = (
    "Usage: /memory [status | pending | approve <id|all> | reject <id|all>]\n"
    "  status   — bounded-store usage + pending-write count\n"
    "  pending  — list writes staged by the approval gate\n"
    "  approve  — apply staged write(s) (re-runs scans + budget checks)\n"
    "  reject   — discard staged write(s)\n"
    "(no argument opens the memory-file picker)"
)


def _fmt_usage(store, target: str) -> str:
    current = store._char_count(target)
    limit = store._char_limit(target)
    pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
    return f"{pct}% — {current:,}/{limit:,} chars"


def _status_text() -> str:
    store = get_memory_store()
    # Live state for the operator view (reload so sister-session /
    # external writes show).
    try:
        store._reload_target("memory", skip_drift=True)
        store._reload_target("user", skip_drift=True)
    except Exception:  # noqa: BLE001 — status must not fail on a bad file
        logger.debug("memory status reload failed", exc_info=True)
    lines = [
        f"Memory (MEMORY.md): {len(store.memory_entries)} entries, "
        f"{_fmt_usage(store, 'memory')}",
        f"User profile (USER.md): {len(store.user_entries)} entries, "
        f"{_fmt_usage(store, 'user')}",
        f"Write approval: {'on' if wa.write_approval_enabled() else 'off'}",
    ]
    n = wa.pending_count()
    if n:
        lines.append(f"Pending writes: {n} — review with /memory pending")
    # The background review's spend never enters the session /cost odometer
    # (the fork must not touch session accounting) — surface it here so it
    # isn't invisible.
    try:
        from .review_fork import get_last_review_stats

        stats = get_last_review_stats()
        if stats:
            lines.append(
                f"Last background review: {stats['input_tokens']:,} in / "
                f"{stats['output_tokens']:,} out tokens, "
                f"{stats['duration_s']}s"
            )
    except Exception:  # noqa: BLE001 — stats are decorative
        logger.debug("last-review stats unavailable", exc_info=True)
    return "\n".join(lines)


def _pending_text() -> str:
    records = wa.list_pending()
    if not records:
        return "No pending memory writes."
    lines = [f"{len(records)} pending memory write(s):"]
    for r in records:
        origin = r.get("origin", "foreground")
        lines.append(
            f"  {r.get('id', '?')}  [{origin}] {r.get('summary', '')[:140]}"
        )
    lines.append("Apply with /memory approve <id|all>, discard with /memory reject <id|all>.")
    return "\n".join(lines)


def _approve(selector: str) -> str:
    records = wa.list_pending()
    if not records:
        return "No pending memory writes."
    if selector != "all":
        records = [r for r in records if r.get("id") == selector]
        if not records:
            return f"No pending write with id '{selector}'. See /memory pending."
    store = get_memory_store()
    out: list[str] = []
    for r in records:
        result = wa.apply_memory_pending(r.get("payload") or {}, store)
        if result.get("success"):
            wa.discard_pending(str(r.get("id")))
            out.append(f"  {r.get('id')}: applied — {result.get('message', 'ok')}")
        else:
            # Keep the record so the user can retry after fixing the issue
            # (e.g. over-budget: consolidate first).
            out.append(f"  {r.get('id')}: FAILED — {result.get('error', 'unknown error')}")
    return "\n".join(["Approve results:"] + out)


def _reject(selector: str) -> str:
    records = wa.list_pending()
    if not records:
        return "No pending memory writes."
    if selector != "all":
        records = [r for r in records if r.get("id") == selector]
        if not records:
            return f"No pending write with id '{selector}'. See /memory pending."
    n = 0
    for r in records:
        if wa.discard_pending(str(r.get("id"))):
            n += 1
    return f"Discarded {n} pending memory write(s)."


def handle_memory_manage(arg: str) -> str:
    """Handle ``/memory <arg>``. Never raises."""
    try:
        parts = (arg or "").strip().split()
        if not parts:
            return _USAGE
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub == "status":
            return _status_text()
        if sub == "pending":
            return _pending_text()
        if sub == "approve":
            if not rest:
                return "Usage: /memory approve <id|all>"
            return _approve(rest)
        if sub == "reject":
            if not rest:
                return "Usage: /memory reject <id|all>"
            return _reject(rest)
        return _USAGE
    except Exception as exc:  # noqa: BLE001 — a slash command must not raise
        logger.debug("memory manage failed", exc_info=True)
        return f"memory: error — {exc}"


__all__ = ["handle_memory_manage"]

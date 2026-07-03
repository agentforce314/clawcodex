"""R5 round-5 (ch17) — the date-change (midnight-rollover) companion.

Port of TS ``getDateChangeAttachments`` (``utils/attachments.ts:1416-1444``).

The ``# Environment`` block's date is memoized at session start (round-4 ch17,
for prompt-cache stability): the cached prefix keeps the START date so midnight
doesn't bust ~190K cached tokens. The trade-off is a stale date after midnight.
This companion recovers it: on the first turn after the date rolls over, append
a ``<system-reminder>`` at the conversation TAIL (never the cached prefix)
telling the model today's date. Cheap — a date comparison per turn, emitting
only on rollover.

The last-emitted date lives in the process-global bootstrap state
(``last_emitted_date``), matching TS's module-level ``getLastEmittedDate``. For
the shipped single-session ``--stdio`` interactive lane this is exactly right;
under multi-session ``--http`` the date is a shared wall-clock value, so only
the first session to observe the rollover emits the reminder (a benign
divergence noted for a future per-session refinement).
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _current_date_iso() -> str:
    """Today's local date, ``YYYY-MM-DD`` — LIVE (not the memoized session
    date). This is the value that changes at midnight and drives the check.

    Note: like the memoized env date (``prompt_assembly._get_session_start_
    date_iso``), this does NOT honor TS's ``CLAUDE_CODE_OVERRIDE_DATE`` test
    override — since BOTH ignore it they use the same real wall clock and
    can't disagree (no spurious rollover). A pre-existing ch17 parity gap for
    a future sweep."""
    return datetime.now().strftime("%Y-%m-%d")


def get_date_change_reminder() -> str | None:
    """Return a ``<system-reminder>`` notifying the model that the date rolled
    over, or None. Records the current date on the first call (no reminder)
    and whenever it changes. Mirrors TS ``getDateChangeAttachments``:

    - last == None  → first turn: record, emit nothing.
    - current == last → no change: emit nothing.
    - current != last → rollover: record + emit the new-date reminder.

    Never raises (a bad clock/state read must not block a turn)."""
    try:
        from src.bootstrap.state import (
            get_last_emitted_date,
            set_last_emitted_date,
        )

        current = _current_date_iso()
        last = get_last_emitted_date()
        if last is None:
            set_last_emitted_date(current)
            return None
        if current == last:
            return None
        set_last_emitted_date(current)
        # Exact TS text (messages.ts:4177) — the "DO NOT mention this to the
        # user" clause is a load-bearing behavioral guard (stops the model
        # from announcing the rollover). The reconciliation hint is appended
        # after it (the env block's date is memoized at session start).
        return (
            "<system-reminder>\n"
            f"The date has changed. Today's date is now {current}. DO NOT "
            "mention this to the user explicitly because they are already "
            "aware. (The date in the environment context above was captured "
            "at session start and is now stale; use this one.)\n"
            "</system-reminder>"
        )
    except Exception:  # noqa: BLE001 — a date-change check must not block a turn
        logger.debug("date-change reminder failed", exc_info=True)
        return None

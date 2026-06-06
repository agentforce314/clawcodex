"""Deterministic jitter helpers for Cron tasks (F-22-G2 + G3)."""

from __future__ import annotations

import hashlib
from datetime import datetime

from .models import (
    DEFAULT_RECURRING_CAP_MS,
    DEFAULT_RECURRING_FRAC,
    CronFields,
    CronJitterConfig,
    validate_jitter_config,
)
from .parser import compute_next_cron_run, datetime_to_ms


def jitter_frac(task_id: str) -> float:
    """Hash task_id to a deterministic fraction in [0, 1)."""
    digest = hashlib.sha256(task_id.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    return value / float(2**64 - 1)


def recurring_jitter_ms(task_id: str, interval_ms: int, config: CronJitterConfig) -> int:
    """Forward jitter (G2): proportional to interval, capped.

    Mirrors ``claude-code-best`` ``jitteredNextCronRunMs``: the per-task delay
    is ``jitterFrac * recurringFrac * interval``, clamped to ``recurringCapMs``.
    """
    if interval_ms <= 0:
        return 0
    if not config.enabled:
        return 0
    cap = max(0, int(config.recurring_cap_ms))
    raw = jitter_frac(task_id) * float(config.recurring_frac) * interval_ms
    return int(min(raw, cap))


def one_shot_lead_ms(task_id: str, config: CronJitterConfig) -> int:
    """Backward lead for one-shot tasks (G3): uniform in [floor, max]."""
    if not config.enabled:
        return 0
    floor = max(0, int(config.one_shot_floor_ms))
    cap = max(floor, int(config.one_shot_max_ms))
    return floor + int(jitter_frac(task_id) * (cap - floor))


def jittered_next_cron_run_ms(
    task_id: str,
    fields: CronFields,
    from_time: datetime,
    config: CronJitterConfig | None = None,
) -> int | None:
    """Recurring-task next fire (forward jitter).

    Falls back to legacy single ``max_jitter_ms`` semantics when the
    recurringFrac/cap pair is zero — preserves backward compat with tasks
    written before G2 shipped.
    """
    cfg = validate_jitter_config(config)
    next_run = compute_next_cron_run(fields, from_time)
    if next_run is None:
        return None

    base_ms = datetime_to_ms(next_run)
    if not cfg.enabled:
        return base_ms

    # Backward-compat: pre-G2 configs use max_jitter_ms with a flat add.
    legacy = max(0, int(cfg.max_jitter_ms))
    if cfg.recurring_frac == 0 or cfg.recurring_cap_ms == 0:
        if legacy > 0:
            return base_ms + int(jitter_frac(task_id) * legacy)
        return base_ms

    # Recurring forward jitter: cap proportional to interval between fires.
    next_after = compute_next_cron_run(fields, next_run + _one_second())
    interval = (
        datetime_to_ms(next_after) - base_ms
        if next_after is not None
        else 0
    )
    return base_ms + recurring_jitter_ms(task_id, interval, cfg)


def one_shot_jittered_next_cron_run_ms(
    task_id: str,
    fields: CronFields,
    from_time: datetime,
    config: CronJitterConfig | None = None,
) -> int | None:
    """One-shot task next fire (backward jitter, minute-gated).

    Mirrors ``claude-code-best`` ``oneShotJitteredNextCronRunMs``:
      * If the computed fire minute does NOT match ``minute % mod == 0``,
        fire on the mark (no lead).
      * Otherwise subtract a uniform lead in [floor, max], clamped so a
        task never fires before it was created.
    """
    cfg = validate_jitter_config(config)
    next_run = compute_next_cron_run(fields, from_time)
    if next_run is None:
        return None

    base_ms = datetime_to_ms(next_run)
    if not cfg.enabled:
        return base_ms

    # Cron resolution is 1 minute; local minute suffices (matches CCB).
    mod = max(1, int(cfg.one_shot_minute_mod))
    if next_run.minute % mod != 0:
        return base_ms

    lead = one_shot_lead_ms(task_id, cfg)
    created_ms = datetime_to_ms(from_time)
    return max(base_ms - lead, created_ms)


def _one_second():
    from datetime import timedelta
    return timedelta(seconds=1)


__all__ = [
    "DEFAULT_RECURRING_CAP_MS",
    "DEFAULT_RECURRING_FRAC",
    "jitter_frac",
    "jittered_next_cron_run_ms",
    "one_shot_jittered_next_cron_run_ms",
    "one_shot_lead_ms",
    "recurring_jitter_ms",
]

"""Deterministic jitter helpers for Cron tasks."""

from __future__ import annotations

import hashlib
from datetime import datetime

from .models import CronFields, CronJitterConfig
from .parser import compute_next_cron_run, datetime_to_ms


def jitter_frac(task_id: str) -> float:
    digest = hashlib.sha256(task_id.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    return value / float(2**64 - 1)


def validate_jitter_config(config: CronJitterConfig | None) -> CronJitterConfig:
    if config is None:
        return CronJitterConfig()
    return CronJitterConfig(enabled=config.enabled, max_jitter_ms=max(0, int(config.max_jitter_ms)))


def jittered_next_cron_run_ms(
    task_id: str,
    fields: CronFields,
    from_time: datetime,
    config: CronJitterConfig | None = None,
) -> int | None:
    next_run = compute_next_cron_run(fields, from_time)
    if next_run is None:
        return None

    base_ms = datetime_to_ms(next_run)
    jitter = validate_jitter_config(config)
    if not jitter.enabled or jitter.max_jitter_ms <= 0:
        return base_ms
    return base_ms + int(jitter_frac(task_id) * jitter.max_jitter_ms)


def one_shot_jittered_next_cron_run_ms(
    task_id: str,
    fields: CronFields,
    from_time: datetime,
    config: CronJitterConfig | None = None,
) -> int | None:
    return jittered_next_cron_run_ms(task_id, fields, from_time, config)

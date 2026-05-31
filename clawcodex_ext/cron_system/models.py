"""Models for the downstream Cron execution engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEDULED_TASKS_RELATIVE_PATH = Path(".claude/scheduled_tasks.json")
SCHEDULED_TASKS_LOCK_RELATIVE_PATH = Path(".claude/scheduled_tasks.lock")
SCHEDULED_TASKS_STORAGE_LOCK_RELATIVE_PATH = Path(".claude/scheduled_tasks.storage.lock")
DEFAULT_RECURRING_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
DEFAULT_JITTER_MS = 30_000


@dataclass(frozen=True)
class CronFields:
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]


@dataclass(frozen=True)
class CronJitterConfig:
    enabled: bool = True
    max_jitter_ms: int = DEFAULT_JITTER_MS


@dataclass(frozen=True)
class CronTask:
    id: str
    cron: str
    prompt: str
    recurring: bool = True
    durable: bool = False
    created_at: int = 0
    updated_at: int = 0
    last_fired_at: int | None = None
    next_fire_at: int | None = None
    expires_at: int | None = None
    jitter: CronJitterConfig = CronJitterConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronTask | None:
        try:
            task_id = data["id"]
            cron = data["cron"]
            prompt = data["prompt"]
            if not isinstance(task_id, str) or not task_id:
                return None
            if not isinstance(cron, str) or not cron.strip():
                return None
            if not isinstance(prompt, str) or not prompt.strip():
                return None
            jitter_data = data.get("jitter") or {}
            if not isinstance(jitter_data, dict):
                jitter_data = {}
            return cls(
                id=task_id,
                cron=cron,
                prompt=prompt,
                recurring=bool(data.get("recurring", True)),
                durable=bool(data.get("durable", False)),
                created_at=int(data.get("created_at") or data.get("createdAt") or 0),
                updated_at=int(data.get("updated_at") or data.get("updatedAt") or 0),
                last_fired_at=_optional_int(data.get("last_fired_at", data.get("lastFiredAt"))),
                next_fire_at=_optional_int(data.get("next_fire_at", data.get("nextFireAt"))),
                expires_at=_optional_int(data.get("expires_at", data.get("expiresAt"))),
                jitter=CronJitterConfig(
                    enabled=bool(jitter_data.get("enabled", True)),
                    max_jitter_ms=max(0, int(jitter_data.get("max_jitter_ms", jitter_data.get("maxJitterMs", DEFAULT_JITTER_MS)))),
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cron": self.cron,
            "prompt": self.prompt,
            "recurring": self.recurring,
            "durable": self.durable,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_fired_at": self.last_fired_at,
            "next_fire_at": self.next_fire_at,
            "expires_at": self.expires_at,
            "jitter": {
                "enabled": self.jitter.enabled,
                "max_jitter_ms": self.jitter.max_jitter_ms,
            },
        }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)

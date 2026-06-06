"""Models for the downstream Cron execution engine."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

SCHEDULED_TASKS_RELATIVE_PATH = Path(".claude/scheduled_tasks.json")
SCHEDULED_TASKS_LOCK_RELATIVE_PATH = Path(".claude/scheduled_tasks.lock")
SCHEDULED_TASKS_STORAGE_LOCK_RELATIVE_PATH = Path(".claude/scheduled_tasks.storage.lock")
SCHEDULED_TASKS_CONFIG_RELATIVE_PATH = Path(".claude/cron_jitter_config.json")

DEFAULT_RECURRING_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
DEFAULT_RECURRING_FRAC = 0.1
DEFAULT_RECURRING_CAP_MS = 15 * 60 * 1000
DEFAULT_ONE_SHOT_MAX_MS = 90 * 1000
DEFAULT_ONE_SHOT_FLOOR_MS = 0
DEFAULT_ONE_SHOT_MINUTE_MOD = 30
# Defense-in-depth upper bounds (mirrors CCB cronJitterConfig.ts).
MAX_RECURRING_CAP_MS = 30 * 60 * 1000
MAX_ONE_SHOT_MAX_MS = 30 * 60 * 1000
MAX_ONE_SHOT_FLOOR_MS = 30 * 60 * 1000
MAX_ONE_SHOT_MINUTE_MOD = 60
MAX_RECURRING_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000
ENV_CLAWCODEX_DISABLE_CRON = "CLAWCODEX_DISABLE_CRON"


def _default_jitter_config() -> "CronJitterConfig":
    return CronJitterConfig(
        recurring_frac=DEFAULT_RECURRING_FRAC,
        recurring_cap_ms=DEFAULT_RECURRING_CAP_MS,
        one_shot_max_ms=DEFAULT_ONE_SHOT_MAX_MS,
        one_shot_floor_ms=DEFAULT_ONE_SHOT_FLOOR_MS,
        one_shot_minute_mod=DEFAULT_ONE_SHOT_MINUTE_MOD,
        recurring_max_age_ms=DEFAULT_RECURRING_MAX_AGE_MS,
    )


@dataclass(frozen=True)
class CronFields:
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]


@dataclass(frozen=True)
class CronJitterConfig:
    """Tuning knobs for cron scheduling jitter (mirrors CCB tengu_kairos_cron_config).

    All values are mutable at runtime via :func:`load_jitter_config` which
    reads from ``.claude/cron_jitter_config.json`` and ``CLAWCODEX_CRON_*``
    env vars, falling back to safe defaults on parse error or out-of-range.
    """

    enabled: bool = True
    max_jitter_ms: int = DEFAULT_RECURRING_CAP_MS
    # New F-22-G2 / G3 fields. Field names use snake_case for Python; readers
    # accept both snake_case and camelCase from JSON.
    recurring_frac: float = DEFAULT_RECURRING_FRAC
    recurring_cap_ms: int = DEFAULT_RECURRING_CAP_MS
    one_shot_max_ms: int = DEFAULT_ONE_SHOT_MAX_MS
    one_shot_floor_ms: int = DEFAULT_ONE_SHOT_FLOOR_MS
    one_shot_minute_mod: int = DEFAULT_ONE_SHOT_MINUTE_MOD
    recurring_max_age_ms: int = DEFAULT_RECURRING_MAX_AGE_MS


def validate_jitter_config(config: CronJitterConfig | None) -> CronJitterConfig:
    """Clamp a config to safe ranges, returning defaults if input is None."""
    if config is None:
        return _default_jitter_config()
    return CronJitterConfig(
        enabled=bool(config.enabled),
        max_jitter_ms=max(0, min(MAX_RECURRING_CAP_MS, int(config.max_jitter_ms))),
        recurring_frac=_clamp_fraction(config.recurring_frac),
        recurring_cap_ms=max(0, min(MAX_RECURRING_CAP_MS, int(config.recurring_cap_ms))),
        one_shot_max_ms=max(0, min(MAX_ONE_SHOT_MAX_MS, int(config.one_shot_max_ms))),
        one_shot_floor_ms=max(
            0, min(MAX_ONE_SHOT_FLOOR_MS, int(config.one_shot_floor_ms))
        ),
        one_shot_minute_mod=max(1, min(MAX_ONE_SHOT_MINUTE_MOD, int(config.one_shot_minute_mod))),
        recurring_max_age_ms=max(0, min(MAX_RECURRING_MAX_AGE_MS, int(config.recurring_max_age_ms))),
    )


def _clamp_fraction(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return DEFAULT_RECURRING_FRAC
    if f < 0:
        return 0.0
    if f >= 1.0:
        return 0.999999
    return f


def jitter_config_from_dict(data: dict[str, Any]) -> CronJitterConfig:
    """Build a CronJitterConfig from a dict, accepting snake_case or camelCase keys.

    Unknown / malformed fields are silently ignored — the corresponding default
    is used. Callers should pass the result through :func:`validate_jitter_config`
    to clamp out-of-range values.
    """
    if not isinstance(data, dict):
        return _default_jitter_config()

    def _get(*keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return default

    return CronJitterConfig(
        enabled=bool(_get("enabled", default=True)),
        max_jitter_ms=int(_get("max_jitter_ms", "maxJitterMs", default=DEFAULT_RECURRING_CAP_MS)),
        recurring_frac=_get("recurring_frac", "recurringFrac", default=DEFAULT_RECURRING_FRAC),
        recurring_cap_ms=int(_get("recurring_cap_ms", "recurringCapMs", default=DEFAULT_RECURRING_CAP_MS)),
        one_shot_max_ms=int(_get("one_shot_max_ms", "oneShotMaxMs", default=DEFAULT_ONE_SHOT_MAX_MS)),
        one_shot_floor_ms=int(_get("one_shot_floor_ms", "oneShotFloorMs", default=DEFAULT_ONE_SHOT_FLOOR_MS)),
        one_shot_minute_mod=int(_get("one_shot_minute_mod", "oneShotMinuteMod", default=DEFAULT_ONE_SHOT_MINUTE_MOD)),
        recurring_max_age_ms=int(
            _get(
                "recurring_max_age_ms",
                "recurringMaxAgeMs",
                default=DEFAULT_RECURRING_MAX_AGE_MS,
            )
        ),
    )


def load_jitter_config(
    workspace_root: Path | None = None,
    *,
    env: dict[str, str] | None = None,
) -> CronJitterConfig:
    """Load jitter config from optional file + env vars, fall back to defaults.

    Resolution order (later wins):
      1. Built-in defaults
      2. ``<workspace_root>/.claude/cron_jitter_config.json`` (if present)
      3. ``CLAWCODEX_CRON_*`` environment variables

    Used by :class:`CronScheduler` on every ``check_once()`` tick so config
    changes (file edit, ``export CLAWCODEX_CRON_RECURRING_CAP_MS=...``) take
    effect without restarting the CLI.
    """
    env_map = env if env is not None else os.environ
    base = _default_jitter_config()
    if workspace_root is not None:
        config_path = workspace_root / SCHEDULED_TASKS_CONFIG_RELATIVE_PATH
        if config_path.exists():
            try:
                import json as _json
                raw = _json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                _log.warning("failed to read %s: %s; using defaults", config_path, exc)
                raw = None
            if isinstance(raw, dict):
                base = jitter_config_from_dict(raw)

    overrides: dict[str, Any] = {}
    env_keys = {
        "enabled": "CLAWCODEX_CRON_ENABLED",
        "max_jitter_ms": "CLAWCODEX_CRON_MAX_JITTER_MS",
        "recurring_frac": "CLAWCODEX_CRON_RECURRING_FRAC",
        "recurring_cap_ms": "CLAWCODEX_CRON_RECURRING_CAP_MS",
        "one_shot_max_ms": "CLAWCODEX_CRON_ONE_SHOT_MAX_MS",
        "one_shot_floor_ms": "CLAWCODEX_CRON_ONE_SHOT_FLOOR_MS",
        "one_shot_minute_mod": "CLAWCODEX_CRON_ONE_SHOT_MINUTE_MOD",
        "recurring_max_age_ms": "CLAWCODEX_CRON_RECURRING_MAX_AGE_MS",
    }
    for field_name, env_key in env_keys.items():
        raw = env_map.get(env_key)
        if raw is None or raw == "":
            continue
        if field_name == "enabled":
            overrides[field_name] = raw.strip().lower() in {"1", "true", "yes", "on"}
        elif field_name == "recurring_frac":
            try:
                overrides[field_name] = float(raw)
            except ValueError:
                _log.warning("invalid float for %s=%r; ignored", env_key, raw)
        else:
            try:
                overrides[field_name] = int(raw)
            except ValueError:
                _log.warning("invalid int for %s=%r; ignored", env_key, raw)

    if overrides:
        merged_dict = {**base.__dict__, **overrides}
        try:
            base = CronJitterConfig(**merged_dict)
        except TypeError as exc:
            _log.warning("invalid jitter config overrides: %s; ignored", exc)

    return validate_jitter_config(base)


def is_cron_disabled(env: dict[str, str] | None = None) -> bool:
    """Check ``CLAWCODEX_DISABLE_CRON`` (F-22-G1) at runtime."""
    env_map = env if env is not None else os.environ
    raw = env_map.get(ENV_CLAWCODEX_DISABLE_CRON)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    jitter: CronJitterConfig = None  # type: ignore[assignment]
    permanent: bool = False

    def __post_init__(self) -> None:
        # frozen dataclass: bypass __setattr__ via object.__setattr__
        if self.jitter is None:
            object.__setattr__(self, "jitter", _default_jitter_config())

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
                jitter=jitter_config_from_dict(jitter_data),
                permanent=bool(data.get("permanent", False)),
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
                "recurring_frac": self.jitter.recurring_frac,
                "recurring_cap_ms": self.jitter.recurring_cap_ms,
                "one_shot_max_ms": self.jitter.one_shot_max_ms,
                "one_shot_floor_ms": self.jitter.one_shot_floor_ms,
                "one_shot_minute_mod": self.jitter.one_shot_minute_mod,
                "recurring_max_age_ms": self.jitter.recurring_max_age_ms,
            },
            "permanent": self.permanent,
        }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)

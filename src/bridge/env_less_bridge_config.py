"""Env-less (v2) bridge timing config.

Ports ``typescript/src/bridge/envLessBridgeConfig.ts``.

Defines the per-session timing knobs used by the v2 (env-less) bridge
path: init retry backoff, HTTP timeouts, JWT refresh buffer, heartbeat
cadence, archive teardown deadline, connect timeout, version floor, and
the app-upgrade nudge bit. Numeric values match TS exactly so the port is
behavior-preserving — see ``test_env_less_bridge_config.py`` for the
contract.

Per refactoring plan §0.1 Q2, GrowthBook is not wired in the Python build;
``get_env_less_bridge_config()`` returns the validated defaults rather than
fetching ``tengu_bridge_repl_v2_config`` from the GrowthBook client. When
GB lands in Phase 10, the function swaps to fetch + validate; callers see
no API change.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.bridge.bridge_enabled import is_env_less_bridge_enabled


class EnvLessBridgeConfig(BaseModel):
    """Validated env-less bridge timing config.

    Mirrors TS ``EnvLessBridgeConfig`` on ``envLessBridgeConfig.ts:7-42``.
    Field names + numeric values + bounds match TS Zod schema exactly.

    Bounds are enforced by ``Field(..., ge=..., le=...)``; out-of-range
    inputs trigger a ``ValidationError`` and the caller falls back to
    ``DEFAULT_ENV_LESS_BRIDGE_CONFIG`` (matching the TS pattern of
    rejecting the whole object on any field violation).

    ``strict=True`` matches TS Zod's no-coercion semantics: ``z.number()``
    rejects strings, ``z.boolean()`` rejects strings — pydantic v2 with
    ``strict=True`` does the same. Without this, a GrowthBook payload
    with JSON-string integers ("5" instead of 5) would silently coerce
    and validate, hiding upstream schema drift.
    """

    model_config = ConfigDict(strict=True)

    init_retry_max_attempts: int = Field(default=3, ge=1, le=10)
    init_retry_base_delay_ms: int = Field(default=500, ge=100)
    init_retry_jitter_fraction: float = Field(default=0.25, ge=0.0, le=1.0)
    init_retry_max_delay_ms: int = Field(default=4000, ge=500)
    http_timeout_ms: int = Field(default=10_000, ge=2000)
    uuid_dedup_buffer_size: int = Field(default=2000, ge=100, le=50_000)
    # Server TTL is 60s. Floor 5s prevents thrash; cap 30s keeps ≥2× margin.
    heartbeat_interval_ms: int = Field(default=20_000, ge=5_000, le=30_000)
    # ±fraction per beat. Cap 0.5: at 30s × 1.5 = 45s worst case, still
    # under the 60s server TTL.
    heartbeat_jitter_fraction: float = Field(default=0.1, ge=0.0, le=0.5)
    # Floor 30s prevents tight-looping. Cap 30min rejects buffer-vs-delay
    # semantic inversion (see TS comment lines 80-86).
    token_refresh_buffer_ms: int = Field(
        default=300_000, ge=30_000, le=1_800_000
    )
    # Cap 2000 keeps this under gracefulShutdown's 2s cleanup race.
    teardown_archive_timeout_ms: int = Field(default=1500, ge=500, le=2000)
    # Observed p99 connect ~2-3s; 15s = ~5× headroom. Floor 5s, cap 60s.
    connect_timeout_ms: int = Field(default=15_000, ge=5_000, le=60_000)
    min_version: str = Field(default='0.0.0')
    should_show_app_upgrade_message: bool = False


DEFAULT_ENV_LESS_BRIDGE_CONFIG: EnvLessBridgeConfig = EnvLessBridgeConfig()
"""Defaults matching TS ``DEFAULT_ENV_LESS_BRIDGE_CONFIG`` on
``envLessBridgeConfig.ts:44-58`` exactly. See ``test_env_less_bridge_config.py``
for the per-field assertion contract.
"""


async def get_env_less_bridge_config() -> EnvLessBridgeConfig:
    """Fetch + validate the env-less bridge timing config.

    Mirrors TS ``getEnvLessBridgeConfig`` on ``envLessBridgeConfig.ts:130-137``.
    The TS version fetches ``tengu_bridge_repl_v2_config`` from GrowthBook
    and parses it through the Zod schema; on any validation error, falls
    back to the defaults. The Python port returns defaults directly — see
    module docstring for the rationale.
    """
    return DEFAULT_ENV_LESS_BRIDGE_CONFIG


def validate_env_less_bridge_config_raw(
    raw: object,
) -> EnvLessBridgeConfig:
    """Validate a raw dict against the schema; fall back to defaults on error.

    Public helper for downstream callers (Phase 10 GrowthBook integration
    or test code) that have a raw payload and want the same "reject the
    whole object on any field violation" semantics TS Zod provides.
    """
    if not isinstance(raw, dict):
        return DEFAULT_ENV_LESS_BRIDGE_CONFIG
    try:
        return EnvLessBridgeConfig.model_validate(raw)
    except ValidationError:
        return DEFAULT_ENV_LESS_BRIDGE_CONFIG


async def check_env_less_bridge_min_version() -> str | None:
    """Returns an error message if CLI version is below v2 min, else None.

    Mirrors TS ``checkEnvLessBridgeMinVersion`` on
    ``envLessBridgeConfig.ts:147-153``. The Python build has no semver
    comparator wired and no GrowthBook-served min_version (default
    ``'0.0.0'`` passes everything), so this always returns ``None``.
    """
    cfg = await get_env_less_bridge_config()
    if cfg.min_version == '0.0.0':
        return None
    # When a real min_version arrives via Phase 10 GB integration, swap
    # this branch for a real semver comparison.
    return None


async def should_show_app_upgrade_message() -> bool:
    """Whether to nudge users toward upgrading their claude.ai app.

    Mirrors TS ``shouldShowAppUpgradeMessage`` on
    ``envLessBridgeConfig.ts:161-165``. True only when (a) the v2 bridge
    is active AND (b) the config bit is set.
    """
    if not is_env_less_bridge_enabled():
        return False
    cfg = await get_env_less_bridge_config()
    return cfg.should_show_app_upgrade_message


__all__ = [
    'DEFAULT_ENV_LESS_BRIDGE_CONFIG',
    'EnvLessBridgeConfig',
    'check_env_less_bridge_min_version',
    'get_env_less_bridge_config',
    'should_show_app_upgrade_message',
    'validate_env_less_bridge_config_raw',
]

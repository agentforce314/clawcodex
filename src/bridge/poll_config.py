"""Bridge poll-loop interval validator + accessor.

Ports ``typescript/src/bridge/pollConfig.ts``.

Pydantic v2 schema for the bridge poll-interval config served via the
``tengu_bridge_poll_interval_config`` GrowthBook feature. The schema has
two cross-field validators that enforce **at-capacity liveness**:

* (single-session) heartbeat > 0 OR ``poll_interval_ms_at_capacity`` > 0
* (multisession)   heartbeat > 0 OR ``multisession_poll_interval_ms_at_capacity`` > 0

Without those refinements, a fat-fingered config that disables both
heartbeat AND at-capacity poll would tight-loop the bridge's throttle
sites at HTTP-round-trip speed (no sleep between checks). Same defense
the TS Zod schema provides via ``.refine()`` on
``pollConfig.ts:74-91``.

Per refactoring plan §0.1 Q2, GrowthBook is not wired in the Python
build; ``get_poll_interval_config()`` returns ``DEFAULT_POLL_CONFIG``
directly. The validator exists for Phase 10 / test code that wants to
exercise the schema on raw inputs.
"""

from __future__ import annotations

from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from src.bridge.poll_config_defaults import (
    DEFAULT_POLL_CONFIG,
    PollIntervalConfig,
)


class _PollIntervalSchema(BaseModel):
    """Pydantic validator that mirrors TS Zod ``pollIntervalConfigSchema``.

    Field-level bounds + cross-field refines + per-field defaults match
    TS exactly. After validation, ``.to_dataclass()`` converts to the
    frozen ``PollIntervalConfig`` dataclass that the rest of the codebase
    consumes (the dataclass is hashable + frozen — properties the poll
    loop relies on).

    ``poll_interval_ms_at_capacity`` and the multisession at-capacity
    variant accept 0 (= disabled) OR >= 100; values 1-99 are rejected to
    catch unit-confusion (ops thinks "seconds", enters 10).

    ``strict=True`` matches TS Zod's no-coercion semantics. See
    ``env_less_bridge_config.EnvLessBridgeConfig`` for the same rationale.
    """

    model_config = ConfigDict(strict=True)

    poll_interval_ms_not_at_capacity: int = Field(
        default=DEFAULT_POLL_CONFIG.poll_interval_ms_not_at_capacity,
        ge=100,
    )
    poll_interval_ms_at_capacity: int = Field(
        default=DEFAULT_POLL_CONFIG.poll_interval_ms_at_capacity,
    )
    non_exclusive_heartbeat_interval_ms: int = Field(
        default=DEFAULT_POLL_CONFIG.non_exclusive_heartbeat_interval_ms,
        ge=0,
    )
    multisession_poll_interval_ms_not_at_capacity: int = Field(
        default=DEFAULT_POLL_CONFIG.multisession_poll_interval_ms_not_at_capacity,
        ge=100,
    )
    multisession_poll_interval_ms_partial_capacity: int = Field(
        default=DEFAULT_POLL_CONFIG.multisession_poll_interval_ms_partial_capacity,
        ge=100,
    )
    multisession_poll_interval_ms_at_capacity: int = Field(
        default=DEFAULT_POLL_CONFIG.multisession_poll_interval_ms_at_capacity,
    )
    reclaim_older_than_ms: int = Field(
        default=DEFAULT_POLL_CONFIG.reclaim_older_than_ms,
        ge=1,
    )
    session_keepalive_interval_v2_ms: int = Field(
        default=DEFAULT_POLL_CONFIG.session_keepalive_interval_v2_ms,
        ge=0,
    )

    @model_validator(mode='after')
    def _validate_at_capacity_liveness(self) -> Self:
        """Enforce the two TS ``.refine()`` cross-field invariants.

        Mirrors TS ``pollConfig.ts:74-91``. Both single-session AND
        multisession at-capacity loops must have *some* liveness signal:
        either a positive heartbeat interval or a positive at-capacity
        poll interval. All-zero is rejected; the caller falls back to
        ``DEFAULT_POLL_CONFIG`` via ``get_poll_interval_config``.
        """
        if (
            self.non_exclusive_heartbeat_interval_ms == 0
            and self.poll_interval_ms_at_capacity == 0
        ):
            raise ValueError(
                'at-capacity liveness requires '
                'non_exclusive_heartbeat_interval_ms > 0 or '
                'poll_interval_ms_at_capacity > 0'
            )
        if (
            self.non_exclusive_heartbeat_interval_ms == 0
            and self.multisession_poll_interval_ms_at_capacity == 0
        ):
            raise ValueError(
                'at-capacity liveness requires '
                'non_exclusive_heartbeat_interval_ms > 0 or '
                'multisession_poll_interval_ms_at_capacity > 0'
            )
        return self

    @model_validator(mode='after')
    def _validate_at_capacity_zero_or_min(self) -> Self:
        """``poll_interval_ms_at_capacity`` must be 0 (disabled) OR >= 100.

        Mirrors TS refine on ``pollConfig.ts:34-38`` and ``63-65``. Values
        1-99 are rejected as "unit confusion".
        """
        v = self.poll_interval_ms_at_capacity
        if v != 0 and v < 100:
            raise ValueError(
                'poll_interval_ms_at_capacity must be 0 (disabled) or ≥100ms'
            )
        v = self.multisession_poll_interval_ms_at_capacity
        if v != 0 and v < 100:
            raise ValueError(
                'multisession_poll_interval_ms_at_capacity must be 0 '
                '(disabled) or ≥100ms'
            )
        return self

    def to_dataclass(self) -> PollIntervalConfig:
        """Convert the validated schema model into the frozen dataclass."""
        return PollIntervalConfig(
            poll_interval_ms_not_at_capacity=self.poll_interval_ms_not_at_capacity,
            poll_interval_ms_at_capacity=self.poll_interval_ms_at_capacity,
            non_exclusive_heartbeat_interval_ms=self.non_exclusive_heartbeat_interval_ms,
            multisession_poll_interval_ms_not_at_capacity=self.multisession_poll_interval_ms_not_at_capacity,
            multisession_poll_interval_ms_partial_capacity=self.multisession_poll_interval_ms_partial_capacity,
            multisession_poll_interval_ms_at_capacity=self.multisession_poll_interval_ms_at_capacity,
            reclaim_older_than_ms=self.reclaim_older_than_ms,
            session_keepalive_interval_v2_ms=self.session_keepalive_interval_v2_ms,
        )


def get_poll_interval_config() -> PollIntervalConfig:
    """Return the validated poll-interval config.

    Mirrors TS ``getPollIntervalConfig`` on ``pollConfig.ts:102-110``.
    The TS version fetches ``tengu_bridge_poll_interval_config`` from
    GrowthBook and validates it; on failure, falls back to defaults.
    Python returns the defaults directly — no GrowthBook in this build.
    """
    return DEFAULT_POLL_CONFIG


def validate_poll_interval_config_raw(raw: object) -> PollIntervalConfig:
    """Validate a raw dict against the schema; fall back to defaults on error.

    Public helper for downstream callers (Phase 10 GrowthBook integration
    or tests) that have a raw payload and want TS Zod's "reject the whole
    object on any field violation" semantics.
    """
    if not isinstance(raw, dict):
        return DEFAULT_POLL_CONFIG
    try:
        model = _PollIntervalSchema.model_validate(raw)
    except ValidationError:
        return DEFAULT_POLL_CONFIG
    return model.to_dataclass()


__all__ = [
    'get_poll_interval_config',
    'validate_poll_interval_config_raw',
]

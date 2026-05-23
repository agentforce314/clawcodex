"""Bridge poll interval default constants.

Ports ``typescript/src/bridge/pollConfigDefaults.ts``.

Extracted from ``poll_config.py`` (Phase 2) so callers that don't need live
GrowthBook tuning (e.g. daemon via Agent SDK) can import the defaults
without pulling in the schema validation layer. Matches TS organization on
``pollConfigDefaults.ts:1-7``.

Numeric values match TS exactly; tests in ``test_poll_config_defaults.py``
assert this for every field.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Numeric constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_MS_NOT_AT_CAPACITY: int = 2000
"""Poll interval when actively seeking work (no transport / below maxSessions).

Mirrors TS ``POLL_INTERVAL_MS_NOT_AT_CAPACITY`` on ``pollConfigDefaults.ts:13``.
"""

POLL_INTERVAL_MS_AT_CAPACITY: int = 600_000
"""Poll interval when the transport is connected. 10 minutes.

Mirrors TS ``POLL_INTERVAL_MS_AT_CAPACITY`` on ``pollConfigDefaults.ts:30``.
Bounded by the server's BRIDGE_LAST_POLL_TTL (4h) and max_poll_stale_seconds
(24h); 10min gives 24× headroom on the Redis TTL.
"""

MULTISESSION_POLL_INTERVAL_MS_NOT_AT_CAPACITY: int = POLL_INTERVAL_MS_NOT_AT_CAPACITY
MULTISESSION_POLL_INTERVAL_MS_PARTIAL_CAPACITY: int = POLL_INTERVAL_MS_NOT_AT_CAPACITY
MULTISESSION_POLL_INTERVAL_MS_AT_CAPACITY: int = POLL_INTERVAL_MS_AT_CAPACITY


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PollIntervalConfig:
    """Tunable poll-loop intervals for the bridge.

    Mirrors TS ``PollIntervalConfig`` on ``pollConfigDefaults.ts:44-53``.
    Frozen because instances are shared across the poll loop (single source
    of truth per call); mutations would race with the loop reading the
    fields.

    Field naming follows the TS wire-format snake_case (matches what
    GrowthBook served when v1 was production-tuned).
    """

    poll_interval_ms_not_at_capacity: int
    poll_interval_ms_at_capacity: int
    non_exclusive_heartbeat_interval_ms: int
    multisession_poll_interval_ms_not_at_capacity: int
    multisession_poll_interval_ms_partial_capacity: int
    multisession_poll_interval_ms_at_capacity: int
    reclaim_older_than_ms: int
    session_keepalive_interval_v2_ms: int


DEFAULT_POLL_CONFIG: PollIntervalConfig = PollIntervalConfig(
    poll_interval_ms_not_at_capacity=POLL_INTERVAL_MS_NOT_AT_CAPACITY,
    poll_interval_ms_at_capacity=POLL_INTERVAL_MS_AT_CAPACITY,
    non_exclusive_heartbeat_interval_ms=0,
    multisession_poll_interval_ms_not_at_capacity=MULTISESSION_POLL_INTERVAL_MS_NOT_AT_CAPACITY,
    multisession_poll_interval_ms_partial_capacity=MULTISESSION_POLL_INTERVAL_MS_PARTIAL_CAPACITY,
    multisession_poll_interval_ms_at_capacity=MULTISESSION_POLL_INTERVAL_MS_AT_CAPACITY,
    reclaim_older_than_ms=5000,
    session_keepalive_interval_v2_ms=120_000,
)
"""Default poll config matching TS ``DEFAULT_POLL_CONFIG``.

See TS comments at ``pollConfigDefaults.ts:55-82`` for the rationale behind
each value (heartbeat disabled by default, 5s reclaim matches server
DEFAULT_RECLAIM_OLDER_THAN_MS, 2min keepalive prevents upstream-proxy GC).
"""


__all__ = [
    'DEFAULT_POLL_CONFIG',
    'MULTISESSION_POLL_INTERVAL_MS_AT_CAPACITY',
    'MULTISESSION_POLL_INTERVAL_MS_NOT_AT_CAPACITY',
    'MULTISESSION_POLL_INTERVAL_MS_PARTIAL_CAPACITY',
    'POLL_INTERVAL_MS_AT_CAPACITY',
    'POLL_INTERVAL_MS_NOT_AT_CAPACITY',
    'PollIntervalConfig',
]

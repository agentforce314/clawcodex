"""autoFix config — port of autoFixConfig.ts.

``get_auto_fix_config(raw)`` mirrors ``getAutoFixConfig`` + the zod schema:
returns a config only when ``raw`` is a dict, ``enabled`` is truthy, at
least one of ``lint``/``test`` is set (the schema's ``.refine``), and the
numeric bounds hold; otherwise ``None`` (the ``safeParse``-returns-null
posture — never raises).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_MAX_RETRIES_DEFAULT = 3
_MAX_RETRIES_LO, _MAX_RETRIES_HI = 0, 10
_TIMEOUT_DEFAULT = 30000
_TIMEOUT_LO, _TIMEOUT_HI = 1000, 300000


@dataclass(frozen=True)
class AutoFixConfig:
    enabled: bool
    lint: str | None = None
    test: str | None = None
    max_retries: int = _MAX_RETRIES_DEFAULT
    timeout_ms: int = _TIMEOUT_DEFAULT


class _Reject(Exception):
    """A field failed zod validation → the whole config is None (safeParse)."""


def _opt_str(value: Any) -> str | None:
    """Zod ``z.string().optional()``: absent → None; a string → itself; a
    present non-string → REJECT the whole config (matching safeParse, not a
    silent drop — critic minor)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Reject
    return value


def _bounded_int(value: Any, default: int, lo: int, hi: int) -> int | None:
    """Zod ``.int().min(lo).max(hi).default(d)``: absent → default; present
    but out of range / non-int → None (parse failure, matching safeParse)."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < lo or value > hi:
        return None
    return value


def get_auto_fix_config(raw: Any) -> AutoFixConfig | None:
    """``settings.autoFix`` → :class:`AutoFixConfig` or ``None``."""
    if not isinstance(raw, dict):
        return None
    # ``enabled: z.boolean()`` is REQUIRED and strict — a string "false" (or
    # any non-bool) rejects the whole config, so it can't turn autoFix ON
    # against apparent intent (critic minor #1).
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        return None
    if not enabled:
        return None
    try:
        lint = _opt_str(raw.get("lint"))
        test = _opt_str(raw.get("test"))
    except _Reject:
        return None
    # The schema's .refine: enabled requires at least one of lint/test.
    if lint is None and test is None:
        return None
    max_retries = _bounded_int(
        raw.get("maxRetries"), _MAX_RETRIES_DEFAULT, _MAX_RETRIES_LO, _MAX_RETRIES_HI
    )
    timeout_ms = _bounded_int(
        raw.get("timeout"), _TIMEOUT_DEFAULT, _TIMEOUT_LO, _TIMEOUT_HI
    )
    if max_retries is None or timeout_ms is None:
        return None
    return AutoFixConfig(
        enabled=True,
        lint=lint,
        test=test,
        max_retries=max_retries,
        timeout_ms=timeout_ms,
    )


def load_auto_fix_config() -> AutoFixConfig | None:
    """Read ``settings.autoFix`` (it lands in ``settings.extra`` — there is
    no typed field). Never raises."""
    try:
        from src.settings.settings import load_settings

        raw = load_settings().extra.get("autoFix")
        return get_auto_fix_config(raw)
    except Exception:  # noqa: BLE001
        return None

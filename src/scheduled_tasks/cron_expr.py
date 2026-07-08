"""5-field vixie-cron expression parsing and next-fire computation.

Implements the exact dialect the CC scheduled-tasks docs promise
(docs/en/scheduled-tasks §Cron expression reference): ``minute hour
day-of-month month day-of-week``; every field supports wildcards (``*``),
single values (``5``), steps (``*/15``), ranges (``1-5``), range-steps
(``1-30/10``) and comma lists (``1,15,30``). Day-of-week accepts ``0`` or
``7`` for Sunday. Extended syntax (``L``, ``W``, ``?``, ``MON``/``JAN``
aliases) is deliberately NOT supported, matching the reference.

When both day-of-month and day-of-week are restricted, a date matches if
EITHER matches (standard vixie-cron semantics). A field is "restricted"
unless its raw text is exactly ``*`` — ``*/2`` counts as restricted.

All computation is in naive local time, mirroring "a cron expression like
``0 9 * * *`` means 9am wherever you're running", not UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["CronExpression", "describe_cron"]

# (min, max) per field, in field order.
_FIELD_BOUNDS: tuple[tuple[int, int], ...] = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day-of-month
    (1, 12),   # month
    (0, 7),    # day-of-week (0 and 7 are both Sunday)
)
_FIELD_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")

# Safety bound for the next-fire search. "Feb 29" normally waits at most
# 4 years, but a non-leap century year stretches the gap to 8 (2096 →
# 2104), so the bound covers that worst case.
_MAX_SEARCH_DAYS = 366 * 8 + 1


def _parse_field(raw: str, lo: int, hi: int, name: str) -> frozenset[int]:
    """Expand one cron field into the set of matching values."""
    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            raise ValueError(f"invalid cron {name} field: empty list item in {raw!r}")
        body, sep, step_text = item.partition("/")
        if sep:
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f"invalid cron {name} step: {item!r}")
            step = int(step_text)
        else:
            step = 1
        if body == "*":
            start, end = lo, hi
        elif "-" in body:
            start_text, _, end_text = body.partition("-")
            if not (start_text.isdigit() and end_text.isdigit()):
                raise ValueError(f"invalid cron {name} range: {item!r}")
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"invalid cron {name} range (start > end): {item!r}")
        elif body.isdigit():
            if sep:  # "5/2" is not vixie syntax
                raise ValueError(f"invalid cron {name} field: step on a single value {item!r}")
            start = end = int(body)
        else:
            raise ValueError(f"invalid cron {name} field: {item!r}")
        if start < lo or end > hi:
            raise ValueError(
                f"cron {name} value out of range {lo}-{hi}: {item!r}"
            )
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError(f"cron {name} field matches nothing: {raw!r}")
    return frozenset(values)


@dataclass(frozen=True)
class CronExpression:
    """A parsed 5-field cron expression. Construct via :meth:`parse`."""

    raw: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]  # normalized: 0=Sunday .. 6=Saturday
    dom_restricted: bool
    dow_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> "CronExpression":
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(
                f"cron expression must have 5 fields (minute hour day month weekday), "
                f"got {len(parts)}: {expression!r}"
            )
        sets = [
            _parse_field(part, lo, hi, name)
            for part, (lo, hi), name in zip(parts, _FIELD_BOUNDS, _FIELD_NAMES)
        ]
        # Fold dow 7 into 0 so matching uses one Sunday value.
        dow = frozenset(0 if v == 7 else v for v in sets[4])
        return cls(
            raw=expression,
            minutes=sets[0],
            hours=sets[1],
            days_of_month=sets[2],
            months=sets[3],
            days_of_week=dow,
            dom_restricted=parts[2] != "*",
            dow_restricted=parts[4] != "*",
        )

    # ── matching ────────────────────────────────────────────────────────

    def _day_matches(self, dt: datetime) -> bool:
        # Python weekday(): Monday=0..Sunday=6 → cron: Sunday=0..Saturday=6.
        cron_dow = (dt.weekday() + 1) % 7
        dom_ok = dt.day in self.days_of_month
        dow_ok = cron_dow in self.days_of_week
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok  # vixie OR semantics
        if self.dom_restricted:
            return dom_ok
        if self.dow_restricted:
            return dow_ok
        return True

    def matches(self, dt: datetime) -> bool:
        return (
            dt.month in self.months
            and self._day_matches(dt)
            and dt.hour in self.hours
            and dt.minute in self.minutes
        )

    def next_after(self, after: datetime) -> datetime:
        """First matching minute strictly after ``after`` (local, naive)."""
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        deadline = candidate + timedelta(days=_MAX_SEARCH_DAYS)
        while candidate < deadline:
            if candidate.month not in self.months:
                # Jump to the first minute of the next month.
                year, month = candidate.year, candidate.month + 1
                if month > 12:
                    year, month = year + 1, 1
                candidate = candidate.replace(
                    year=year, month=month, day=1, hour=0, minute=0
                )
                continue
            if not self._day_matches(candidate):
                candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if candidate.hour not in self.hours:
                candidate = (candidate + timedelta(hours=1)).replace(minute=0)
                continue
            if candidate.minute not in self.minutes:
                candidate += timedelta(minutes=1)
                continue
            return candidate
        # Unreachable for any parseable expression (the parser rejects
        # empty sets and impossible dates just skip forward), but a hard
        # error beats an infinite loop if that invariant ever breaks.
        raise ValueError(f"no fire time within {_MAX_SEARCH_DAYS} days: {self.raw!r}")


def _step_of(values: frozenset[int], lo: int, hi: int) -> int | None:
    """If ``values`` is exactly {lo, lo+s, ...} covering the range, return s."""
    ordered = sorted(values)
    if ordered[0] != lo or len(ordered) == 1:
        return None
    step = ordered[1] - ordered[0]
    if step <= 0:
        return None
    if ordered != list(range(lo, hi + 1, step)):
        return None
    return step


_DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def describe_cron(expression: str) -> str:
    """Best-effort human cadence ("every 5 minutes", "daily at 09:00").

    Falls back to the raw expression for shapes with no simple phrasing.
    Raises ValueError on an invalid expression (same as parsing).
    """
    expr = CronExpression.parse(expression)
    minute_all = len(expr.minutes) == 60
    hour_all = len(expr.hours) == 24
    day_free = not expr.dom_restricted and not expr.dow_restricted and len(expr.months) == 12

    if day_free and hour_all:
        if minute_all:
            return "every minute"
        step = _step_of(expr.minutes, 0, 59)
        if step:
            return f"every {step} minutes"
        if len(expr.minutes) == 1:
            (minute,) = expr.minutes
            return "hourly" if minute == 0 else f"hourly at :{minute:02d}"
    if day_free and len(expr.minutes) == 1:
        (minute,) = expr.minutes
        step = _step_of(expr.hours, 0, 23)
        if step and minute == 0:
            return "hourly" if step == 1 else f"every {step} hours"
        if len(expr.hours) == 1:
            (hour,) = expr.hours
            return f"daily at {hour:02d}:{minute:02d}"
    if (
        not expr.dom_restricted
        and expr.dow_restricted
        and len(expr.months) == 12
        and len(expr.minutes) == 1
        and len(expr.hours) == 1
    ):
        (minute,) = expr.minutes
        (hour,) = expr.hours
        days = ",".join(_DAY_NAMES[d] for d in sorted(expr.days_of_week))
        if sorted(expr.days_of_week) == [1, 2, 3, 4, 5]:
            days = "weekdays"
        return f"{days} at {hour:02d}:{minute:02d}"
    return expression

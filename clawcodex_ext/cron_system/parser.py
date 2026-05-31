"""Cron expression parsing and next-run calculation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import CronFields

_FIELD_RANGES = (
    (0, 59),
    (0, 23),
    (1, 31),
    (1, 12),
    (0, 6),
)


_NAMES = (
    {},
    {},
    {},
    {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    },
    {
        "sun": 0,
        "mon": 1,
        "tue": 2,
        "wed": 3,
        "thu": 4,
        "fri": 5,
        "sat": 6,
    },
)


def parse_cron_expression(expr: str) -> CronFields | None:
    parts = expr.split()
    if len(parts) != 5:
        return None

    parsed: list[frozenset[int]] = []
    for idx, part in enumerate(parts):
        values = _parse_field(part, *_FIELD_RANGES[idx], names=_NAMES[idx], normalize_sunday=idx == 4)
        if values is None:
            return None
        parsed.append(frozenset(values))

    return CronFields(
        minutes=parsed[0],
        hours=parsed[1],
        days_of_month=parsed[2],
        months=parsed[3],
        days_of_week=parsed[4],
    )


def compute_next_cron_run(fields: CronFields, from_time: datetime) -> datetime | None:
    candidate = (from_time + timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = candidate + timedelta(days=366 * 5)
    while candidate <= limit:
        cron_weekday = (candidate.weekday() + 1) % 7
        day_matches = _day_matches(candidate.day, cron_weekday, fields)
        if (
            candidate.minute in fields.minutes
            and candidate.hour in fields.hours
            and day_matches
            and candidate.month in fields.months
        ):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def cron_to_human(cron: str, utc: bool = False) -> str:
    fields = parse_cron_expression(cron)
    if fields is None:
        return cron

    suffix = " UTC" if utc else ""
    minutes, hours, dom, months, dow = cron.split()
    if cron == "* * * * *":
        return f"Every minute{suffix}"
    if hours == "*" and dom == "*" and months == "*" and dow == "*":
        if minutes.startswith("*/"):
            return f"Every {minutes[2:]} minutes{suffix}"
        if minutes.isdigit():
            return f"Hourly at minute {minutes}{suffix}"
    if dom == "*" and months == "*" and dow == "*" and minutes.isdigit():
        if hours.startswith("*/"):
            return f"Every {hours[2:]} hours at minute {minutes}{suffix}"
        if hours.isdigit():
            return f"Daily at {int(hours):02d}:{int(minutes):02d}{suffix}"
    if months == "*" and dow == "*" and minutes.isdigit() and hours.isdigit() and dom.isdigit():
        return f"Monthly on day {int(dom)} at {int(hours):02d}:{int(minutes):02d}{suffix}"
    if months == "*" and dom == "*" and minutes.isdigit() and hours.isdigit() and dow.isdigit():
        return f"Weekly on day {int(dow)} at {int(hours):02d}:{int(minutes):02d}{suffix}"
    return f"Cron schedule {cron}{suffix}"


def _day_matches(day_of_month: int, day_of_week: int, fields: CronFields) -> bool:
    dom_restricted = len(fields.days_of_month) != 31
    dow_restricted = len(fields.days_of_week) != 7
    dom_matches = day_of_month in fields.days_of_month
    dow_matches = day_of_week in fields.days_of_week
    if dom_restricted and dow_restricted:
        return dom_matches or dow_matches
    return dom_matches and dow_matches


def datetime_to_ms(value: datetime) -> int:
    if value.tzinfo is None:
        return int(value.timestamp() * 1000)
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def ms_to_datetime(value: int, tzinfo=timezone.utc) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=tzinfo)


def _parse_field(
    field: str,
    minimum: int,
    maximum: int,
    *,
    names: dict[str, int],
    normalize_sunday: bool = False,
) -> set[int] | None:
    if not field:
        return None
    values: set[int] = set()
    for segment in field.split(","):
        segment_values = _parse_segment(
            segment.strip().lower(),
            minimum,
            maximum,
            names=names,
            normalize_sunday=normalize_sunday,
        )
        if segment_values is None:
            return None
        values.update(segment_values)
    return values or None


def _parse_segment(
    segment: str,
    minimum: int,
    maximum: int,
    *,
    names: dict[str, int],
    normalize_sunday: bool,
) -> set[int] | None:
    if not segment:
        return None

    base, step = segment, 1
    if "/" in segment:
        base, step_text = segment.split("/", 1)
        if not step_text.isdigit():
            return None
        step = int(step_text)
        if step <= 0:
            return None

    if base == "*":
        start, end = minimum, maximum
    elif "-" in base:
        start_text, end_text = base.split("-", 1)
        start = _parse_value(start_text, names=names, normalize_sunday=normalize_sunday)
        end = _parse_value(end_text, names=names, normalize_sunday=normalize_sunday)
        if start is None or end is None or start > end:
            return None
    else:
        value = _parse_value(base, names=names, normalize_sunday=normalize_sunday)
        if value is None:
            return None
        start = end = value

    if start < minimum or end > maximum:
        return None
    return set(range(start, end + 1, step))


def _parse_value(value: str, *, names: dict[str, int], normalize_sunday: bool) -> int | None:
    if value in names:
        return names[value]
    if not value.isdigit():
        return None
    parsed = int(value)
    if normalize_sunday and parsed == 7:
        return 0
    return parsed

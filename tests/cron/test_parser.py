from __future__ import annotations

from datetime import datetime

from clawcodex_ext.cron_system.parser import compute_next_cron_run, cron_to_human, parse_cron_expression


def test_parse_cron_expression_supports_common_forms() -> None:
    assert parse_cron_expression("* * * * *") is not None
    assert parse_cron_expression("*/15 * * * *") is not None
    assert parse_cron_expression("0-30/10 1,2,3 * jan mon-fri") is not None


def test_parse_cron_expression_rejects_invalid_forms() -> None:
    assert parse_cron_expression("* * *") is None
    assert parse_cron_expression("*/0 * * * *") is None
    assert parse_cron_expression("61 * * * *") is None
    assert parse_cron_expression("20-10 * * * *") is None


def test_compute_next_cron_run_is_strictly_future() -> None:
    fields = parse_cron_expression("*/15 * * * *")
    assert fields is not None
    result = compute_next_cron_run(fields, datetime(2026, 1, 1, 12, 0, 0))
    assert result == datetime(2026, 1, 1, 12, 15, 0)


def test_day_of_month_and_day_of_week_use_or_semantics() -> None:
    fields = parse_cron_expression("0 9 15 * 1")
    assert fields is not None
    result = compute_next_cron_run(fields, datetime(2026, 6, 14, 9, 0, 0))
    assert result == datetime(2026, 6, 15, 9, 0, 0)


def test_cron_to_human_common_strings() -> None:
    assert cron_to_human("* * * * *") == "Every minute"
    assert cron_to_human("*/10 * * * *") == "Every 10 minutes"
    assert cron_to_human("0 9 * * *") == "Daily at 09:00"
    assert cron_to_human("0 9 * * *", utc=True) == "Daily at 09:00 UTC"

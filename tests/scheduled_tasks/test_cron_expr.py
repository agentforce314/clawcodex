"""CronExpression: the 5-field vixie dialect the scheduled-tasks docs
promise — wildcards, values, steps, ranges, lists, dow 0/7=Sunday, and the
dom/dow OR rule — plus next-fire computation across boundaries."""

from __future__ import annotations

import unittest
from datetime import datetime

from src.scheduled_tasks import CronExpression, describe_cron


def nxt(expr: str, after: datetime) -> datetime:
    return CronExpression.parse(expr).next_after(after)


class TestParseValidation(unittest.TestCase):
    def test_field_count_enforced(self) -> None:
        for bad in ("* * * *", "* * * * * *", "", "*/5"):
            with self.assertRaises(ValueError):
                CronExpression.parse(bad)

    def test_out_of_range_values_rejected(self) -> None:
        for bad in ("60 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *",
                    "* * * 13 *", "* * * * 8"):
            with self.assertRaises(ValueError):
                CronExpression.parse(bad)

    def test_malformed_tokens_rejected(self) -> None:
        for bad in ("MON * * * *", "*/0 * * * *", "5-1 * * * *",
                    "1,,2 * * * *", "5/2 * * * *", "? * * * *",
                    "L * * * *", "1.5 * * * *"):
            with self.assertRaises(ValueError):
                CronExpression.parse(bad)

    def test_accepted_shapes(self) -> None:
        for ok in ("* * * * *", "*/15 * * * *", "0 9 * * 1-5",
                   "1,15,30 * * * *", "0-30/10 2 * * *", "30 14 15 3 *",
                   "0 0 * * 7"):
            CronExpression.parse(ok)  # must not raise


class TestNextAfter(unittest.TestCase):
    def test_every_five_minutes(self) -> None:
        self.assertEqual(nxt("*/5 * * * *", datetime(2026, 7, 7, 12, 3)),
                         datetime(2026, 7, 7, 12, 5))

    def test_strictly_after_matching_minute(self) -> None:
        # A fire AT 12:05 schedules the NEXT slot, not itself again.
        self.assertEqual(nxt("*/5 * * * *", datetime(2026, 7, 7, 12, 5)),
                         datetime(2026, 7, 7, 12, 10))

    def test_hourly_at_seven_past(self) -> None:
        self.assertEqual(nxt("7 * * * *", datetime(2026, 7, 7, 12, 30)),
                         datetime(2026, 7, 7, 13, 7))

    def test_daily_nine_am_rolls_to_next_day(self) -> None:
        self.assertEqual(nxt("0 9 * * *", datetime(2026, 7, 7, 10, 0)),
                         datetime(2026, 7, 8, 9, 0))

    def test_weekdays_skip_weekend(self) -> None:
        # 2026-07-10 is a Friday; after Friday 9am the next weekday fire
        # is Monday 2026-07-13.
        self.assertEqual(nxt("0 9 * * 1-5", datetime(2026, 7, 10, 9, 0)),
                         datetime(2026, 7, 13, 9, 0))

    def test_sunday_as_seven(self) -> None:
        # 2026-07-07 is a Tuesday → next Sunday is 2026-07-12, and dow=7
        # must behave exactly like dow=0.
        for expr in ("0 0 * * 0", "0 0 * * 7"):
            self.assertEqual(nxt(expr, datetime(2026, 7, 7, 0, 0)),
                             datetime(2026, 7, 12, 0, 0))

    def test_dom_dow_or_semantics(self) -> None:
        # Both restricted → EITHER matches (vixie). "0 0 13 * 5" fires on
        # the 13th AND on every Friday. After 2026-07-07 (Tue): Friday
        # 2026-07-10 comes before Monday the 13th.
        self.assertEqual(nxt("0 0 13 * 5", datetime(2026, 7, 7, 0, 0)),
                         datetime(2026, 7, 10, 0, 0))
        # …and from the 11th, the 13th (Monday) beats next Friday the 17th.
        self.assertEqual(nxt("0 0 13 * 5", datetime(2026, 7, 11, 0, 0)),
                         datetime(2026, 7, 13, 0, 0))

    def test_dom_only_restricted_ignores_dow(self) -> None:
        self.assertEqual(nxt("0 0 13 * *", datetime(2026, 7, 7, 0, 0)),
                         datetime(2026, 7, 13, 0, 0))

    def test_month_and_year_rollover(self) -> None:
        self.assertEqual(nxt("30 14 15 3 *", datetime(2026, 7, 7, 0, 0)),
                         datetime(2027, 3, 15, 14, 30))

    def test_day_31_skips_short_months(self) -> None:
        # From Feb 1 the next 31st is March 31 (Feb has no 31st).
        self.assertEqual(nxt("0 0 31 * *", datetime(2026, 2, 1, 0, 0)),
                         datetime(2026, 3, 31, 0, 0))

    def test_range_step_and_list(self) -> None:
        self.assertEqual(nxt("0-30/10 2 * * *", datetime(2026, 7, 7, 2, 11)),
                         datetime(2026, 7, 7, 2, 20))
        self.assertEqual(nxt("1,15,30 * * * *", datetime(2026, 7, 7, 5, 16)),
                         datetime(2026, 7, 7, 5, 30))

    def test_matches_second_precision_ignored(self) -> None:
        expr = CronExpression.parse("10 12 * * *")
        self.assertTrue(expr.matches(datetime(2026, 7, 7, 12, 10, 59)))
        self.assertFalse(expr.matches(datetime(2026, 7, 7, 12, 11)))


class TestDescribeCron(unittest.TestCase):
    def test_common_phrasings(self) -> None:
        self.assertEqual(describe_cron("* * * * *"), "every minute")
        self.assertEqual(describe_cron("*/5 * * * *"), "every 5 minutes")
        self.assertEqual(describe_cron("7 * * * *"), "hourly at :07")
        self.assertEqual(describe_cron("0 * * * *"), "hourly")
        self.assertEqual(describe_cron("0 */2 * * *"), "every 2 hours")
        self.assertEqual(describe_cron("0 9 * * *"), "daily at 09:00")
        self.assertEqual(describe_cron("0 9 * * 1-5"), "weekdays at 09:00")

    def test_falls_back_to_raw_expression(self) -> None:
        self.assertEqual(describe_cron("30 14 15 3 *"), "30 14 15 3 *")

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            describe_cron("not a cron")


if __name__ == "__main__":
    unittest.main()

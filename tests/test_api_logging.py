from __future__ import annotations

import unittest

from src.services.api.logging import (
    APICallLog,
    NonNullableUsage,
    accumulate_usage,
    update_usage,
)


class TestNonNullableUsage(unittest.TestCase):
    def test_defaults_zero(self) -> None:
        u = NonNullableUsage()
        self.assertEqual(u.input_tokens, 0)
        self.assertEqual(u.output_tokens, 0)
        self.assertEqual(u.cache_creation_input_tokens, 0)
        self.assertEqual(u.cache_read_input_tokens, 0)

    def test_total_tokens(self) -> None:
        u = NonNullableUsage(input_tokens=100, output_tokens=50)
        self.assertEqual(u.total_tokens, 150)

    def test_to_dict(self) -> None:
        u = NonNullableUsage(input_tokens=10, output_tokens=20)
        d = u.to_dict()
        self.assertEqual(d["input_tokens"], 10)
        self.assertEqual(d["output_tokens"], 20)
        self.assertEqual(d["cache_creation_input_tokens"], 0)
        self.assertEqual(d["cache_read_input_tokens"], 0)


class TestAccumulateUsage(unittest.TestCase):
    def test_accumulate_none(self) -> None:
        base = NonNullableUsage(input_tokens=10)
        result = accumulate_usage(base, None)
        self.assertEqual(result.input_tokens, 10)

    def test_accumulate_usage_object(self) -> None:
        base = NonNullableUsage(input_tokens=10, output_tokens=5)
        delta = NonNullableUsage(input_tokens=20, output_tokens=10)
        result = accumulate_usage(base, delta)
        self.assertEqual(result.input_tokens, 30)
        self.assertEqual(result.output_tokens, 15)

    def test_accumulate_dict(self) -> None:
        base = NonNullableUsage(input_tokens=10)
        delta = {"input_tokens": 20, "output_tokens": 5}
        result = accumulate_usage(base, delta)
        self.assertEqual(result.input_tokens, 30)
        self.assertEqual(result.output_tokens, 5)

    def test_accumulate_partial_dict(self) -> None:
        base = NonNullableUsage(input_tokens=10)
        delta = {"input_tokens": 5}
        result = accumulate_usage(base, delta)
        self.assertEqual(result.input_tokens, 15)
        self.assertEqual(result.output_tokens, 0)


class TestUpdateUsage(unittest.TestCase):
    def test_update_none(self) -> None:
        target = NonNullableUsage(input_tokens=10)
        update_usage(target, None)
        self.assertEqual(target.input_tokens, 10)

    def test_update_from_usage(self) -> None:
        target = NonNullableUsage(input_tokens=10)
        source = NonNullableUsage(input_tokens=5, output_tokens=3)
        update_usage(target, source)
        self.assertEqual(target.input_tokens, 15)
        self.assertEqual(target.output_tokens, 3)

    def test_update_from_dict(self) -> None:
        target = NonNullableUsage(input_tokens=10)
        update_usage(target, {"input_tokens": 5, "cache_read_input_tokens": 100})
        self.assertEqual(target.input_tokens, 15)
        self.assertEqual(target.cache_read_input_tokens, 100)


class TestAPICallLog(unittest.TestCase):
    def test_duration_ms(self) -> None:
        log = APICallLog(start_time=1000.0, end_time=1001.5)
        self.assertEqual(log.duration_ms, 1500)

    def test_defaults(self) -> None:
        log = APICallLog()
        self.assertEqual(log.model, "")
        self.assertIsNone(log.error)


if __name__ == "__main__":
    unittest.main()

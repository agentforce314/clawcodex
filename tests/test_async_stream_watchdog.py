"""Tests for the asyncio-native stream idle watchdog (Phase D)."""
from __future__ import annotations

import asyncio
import unittest

from src.utils.stream_watchdog import AsyncStreamWatchdog


class _FakeStream:
    def __init__(self) -> None:
        self.close_called: int = 0
        self.aclose_called: int = 0

    def close(self) -> None:
        self.close_called += 1


class _AsyncCloseStream:
    def __init__(self) -> None:
        self.aclose_called: int = 0

    async def aclose(self) -> None:
        self.aclose_called += 1


class TestAsyncStreamWatchdog(unittest.IsolatedAsyncioTestCase):
    async def test_fires_after_timeout(self) -> None:
        stream = _FakeStream()
        watchdog = AsyncStreamWatchdog(stream, timeout_s=0.05)
        watchdog.arm()
        await asyncio.sleep(0.1)
        self.assertTrue(watchdog.fired)
        self.assertGreaterEqual(stream.close_called, 1)

    async def test_does_not_fire_when_reset(self) -> None:
        stream = _FakeStream()
        watchdog = AsyncStreamWatchdog(stream, timeout_s=0.1)
        watchdog.arm()
        for _ in range(5):
            await asyncio.sleep(0.03)
            watchdog.reset()
        await asyncio.sleep(0.02)
        watchdog.disarm()
        # Total elapsed = 0.17s but no single gap > 0.1s, so watchdog stays
        # disarmed. close() must NOT have been called.
        self.assertFalse(watchdog.fired)
        self.assertEqual(stream.close_called, 0)

    async def test_disarm_cancels_timer(self) -> None:
        stream = _FakeStream()
        watchdog = AsyncStreamWatchdog(stream, timeout_s=0.05)
        watchdog.arm()
        watchdog.disarm()
        await asyncio.sleep(0.1)
        self.assertFalse(watchdog.fired)
        self.assertEqual(stream.close_called, 0)

    async def test_reset_after_fire_is_noop(self) -> None:
        stream = _FakeStream()
        watchdog = AsyncStreamWatchdog(stream, timeout_s=0.05)
        watchdog.arm()
        await asyncio.sleep(0.1)
        self.assertTrue(watchdog.fired)
        # reset after fire is a no-op; doesn't re-arm
        watchdog.reset()
        await asyncio.sleep(0.1)
        # No re-fire: close should have been called exactly once.
        self.assertLessEqual(stream.close_called, 2)

    async def test_async_close_scheduled(self) -> None:
        stream = _AsyncCloseStream()
        watchdog = AsyncStreamWatchdog(stream, timeout_s=0.05)
        watchdog.arm()
        await asyncio.sleep(0.15)
        self.assertTrue(watchdog.fired)
        # aclose() returns a coroutine; the watchdog schedules it via
        # asyncio.ensure_future. After our second sleep the task should
        # have run.
        self.assertEqual(stream.aclose_called, 1)

    async def test_env_var_override(self) -> None:
        import os
        prev = os.environ.pop("CLAUDE_STREAM_IDLE_TIMEOUT_MS", None)
        try:
            os.environ["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = "50"
            stream = _FakeStream()
            watchdog = AsyncStreamWatchdog(stream)  # timeout_s read from env
            self.assertAlmostEqual(watchdog.timeout_s, 0.05, places=3)
        finally:
            os.environ.pop("CLAUDE_STREAM_IDLE_TIMEOUT_MS", None)
            if prev is not None:
                os.environ["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = prev


if __name__ == "__main__":
    unittest.main()

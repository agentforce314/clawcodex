"""Tests for ``src.bridge.jwt_utils.TokenRefreshScheduler``.

We use the running asyncio event loop's ``call_later`` directly (no
``asyncio.sleep`` inside the scheduler). To keep tests fast, callers
schedule short delays and ``await asyncio.sleep`` in the test body to
let the loop fire pending timers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time

import pytest

from src.bridge.jwt_utils import (
    MAX_REFRESH_FAILURES,
    SCHEDULE_FROM_EXPIRES_IN_FLOOR_MS,
    TokenRefreshScheduler,
)


def make_jwt(payload: dict[str, object]) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b'=').decode('ascii')
    body = base64.urlsafe_b64encode(json.dumps(payload).encode('utf-8')).rstrip(b'=').decode('ascii')
    return f'{header}.{body}.signature'


@pytest.mark.asyncio
async def test_immediate_refresh_when_token_already_past_buffer() -> None:
    """Token expires in 1 s, buffer is 5 min — fire immediately."""
    refreshed: list[tuple[str, str]] = []

    sched = TokenRefreshScheduler(
        get_access_token=lambda: 'fresh-token',
        on_refresh=lambda sid, tok: refreshed.append((sid, tok)),
        label='test',
    )
    token = make_jwt({'exp': int(time.time()) + 1})
    sched.schedule('s1', token)

    # Let the immediate fire run.
    await asyncio.sleep(0.05)
    assert refreshed == [('s1', 'fresh-token')]
    sched.cancel_all()


@pytest.mark.asyncio
async def test_schedule_from_expires_in_clamps_to_30s_floor() -> None:
    """When expires_in is small (e.g., shorter than the buffer), clamp to
    ``SCHEDULE_FROM_EXPIRES_IN_FLOOR_MS`` (30 s) — no tight loop.
    """
    sched = TokenRefreshScheduler(
        get_access_token=lambda: 'tok',
        on_refresh=lambda sid, tok: None,
        label='test',
    )
    # expires_in shorter than the 5-min buffer → would be negative without clamp.
    sched.schedule_from_expires_in('s1', 60)
    # Internal: a timer should be scheduled with delay >= 30 s.
    handle = sched._timers.get('s1')  # noqa: SLF001 -- test-only introspection
    assert handle is not None
    # call_later returns a TimerHandle; ``when()`` is monotonic time of fire.
    when = handle.when()
    now = asyncio.get_event_loop().time()
    delay = when - now
    assert delay >= (SCHEDULE_FROM_EXPIRES_IN_FLOOR_MS / 1000) - 0.5  # tolerance
    sched.cancel_all()


@pytest.mark.asyncio
async def test_failure_chain_caps_at_max_refresh_failures() -> None:
    """If get_access_token returns None each time, retries cap at MAX."""
    call_count = 0

    def fake_get_access_token() -> str | None:
        nonlocal call_count
        call_count += 1
        return None

    # Patch the retry interval BEFORE scheduling so we don't wait 60 s
    # between retries. The scheduler reads the constant at the call site
    # inside _do_refresh.
    import src.bridge.jwt_utils as jwt_utils

    original_retry = jwt_utils.REFRESH_RETRY_DELAY_MS
    jwt_utils.REFRESH_RETRY_DELAY_MS = 10

    sched = TokenRefreshScheduler(
        get_access_token=fake_get_access_token,
        on_refresh=lambda sid, tok: None,
        label='test',
        refresh_buffer_ms=1,
    )
    # Token already past — triggers the immediate-fire branch.
    token = make_jwt({'exp': int(time.time()) - 10})
    try:
        sched.schedule('s1', token)
        # Wait long enough for the immediate fire + (MAX-1) retries at 10ms each.
        await asyncio.sleep(0.2)
    finally:
        jwt_utils.REFRESH_RETRY_DELAY_MS = original_retry
        sched.cancel_all()

    # MAX_REFRESH_FAILURES retries should have fired and stopped.
    assert call_count == MAX_REFRESH_FAILURES, (
        f'expected {MAX_REFRESH_FAILURES} attempts, got {call_count}'
    )


@pytest.mark.asyncio
async def test_cancel_invalidates_in_flight_refresh() -> None:
    """If we cancel mid-flight, the in-flight ``_do_refresh`` bails out
    on the generation check before invoking ``on_refresh``.
    """
    refreshed: list[str] = []

    async def slow_get_token() -> str:
        await asyncio.sleep(0.05)
        return 'tok-1'

    sched = TokenRefreshScheduler(
        get_access_token=slow_get_token,
        on_refresh=lambda sid, tok: refreshed.append(sid),
        label='test',
    )
    token = make_jwt({'exp': int(time.time()) + 1})
    sched.schedule('s1', token)

    # Let the refresh fire and start awaiting; then cancel before it
    # resolves.
    await asyncio.sleep(0.01)
    sched.cancel('s1')
    await asyncio.sleep(0.10)

    assert refreshed == [], 'cancelled refresh should not invoke on_refresh'


@pytest.mark.asyncio
async def test_cancel_all_clears_timers_and_failures() -> None:
    sched = TokenRefreshScheduler(
        get_access_token=lambda: 'tok',
        on_refresh=lambda sid, tok: None,
        label='test',
    )
    sched.schedule_from_expires_in('s1', 600)
    sched.schedule_from_expires_in('s2', 600)
    assert len(sched._timers) == 2  # noqa: SLF001
    sched.cancel_all()
    assert len(sched._timers) == 0
    assert len(sched._failure_counts) == 0  # noqa: SLF001

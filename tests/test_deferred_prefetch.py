"""ch02 round-3 GAP B: deferred context prefetch lane.

The lane warms the memoized user/system context
(``context_system.prompt_assembly``) so the first turn's
``fetch_system_prompt_parts`` hits warm caches. System context is
trust-gated via ``bootstrap.state.get_session_trust_accepted`` (TS
``prefetchSystemContextIfSafe``).
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import pytest

import src.context_system.prompt_assembly as pa
from src.bootstrap.state import (
    reset_state_for_tests,
    set_session_trust_accepted,
)
from src.deferred_init import start_deferred_prefetches


@pytest.fixture(autouse=True)
def _clean_context_state():
    pa.clear_context_caches()
    reset_state_for_tests()
    yield
    pa.clear_context_caches()
    reset_state_for_tests()


def _user_cache_warm() -> bool:
    return pa._user_context_cache is not None


def _system_cache_warm() -> bool:
    return pa._system_context_cache is not None


def test_thread_mode_warms_user_context_untrusted(tmp_path):
    handle = start_deferred_prefetches(cwd=str(tmp_path))
    assert handle.mode == "thread"
    handle.join()
    assert _user_cache_warm()
    # Untrusted session: the git-probe lane must not have run.
    assert not _system_cache_warm()


def test_thread_mode_warms_both_when_trusted(tmp_path):
    set_session_trust_accepted(True)
    handle = start_deferred_prefetches(cwd=str(tmp_path))
    handle.join()
    assert _user_cache_warm()
    assert _system_cache_warm()


def test_explicit_include_system_context_overrides_gate(tmp_path):
    # The post-trust-gate re-kick passes True explicitly (the caller just
    # established trust; the flag read would also succeed, but the
    # explicit form documents intent at the call site).
    handle = start_deferred_prefetches(
        cwd=str(tmp_path), include_system_context=True
    )
    handle.join()
    assert _system_cache_warm()


def test_loop_mode_schedules_tasks(tmp_path):
    async def scenario():
        handle = start_deferred_prefetches(cwd=str(tmp_path))
        assert handle.mode == "loop"
        assert handle.tasks
        await asyncio.gather(*handle.tasks)

    asyncio.run(scenario())
    assert _user_cache_warm()


def test_rekick_is_idempotent(tmp_path):
    handle1 = start_deferred_prefetches(cwd=str(tmp_path))
    handle1.join()
    sentinel = pa._user_context_cache
    handle2 = start_deferred_prefetches(
        cwd=str(tmp_path), include_system_context=True
    )
    handle2.join()
    # Memoized: the user-context lane did not recompute.
    assert pa._user_context_cache is sentinel
    assert _system_cache_warm()


def test_prefetch_failure_never_propagates(tmp_path):
    with mock.patch(
        "src.context_system.prompt_assembly.get_user_context",
        side_effect=RuntimeError("boom"),
    ):
        handle = start_deferred_prefetches(cwd=str(tmp_path))
        handle.join()  # must not raise


class TestEntrypointWiring(unittest.TestCase):
    def test_headless_kicks_prefetch_before_provider_load(self) -> None:
        from src.entrypoints import headless

        options = headless.HeadlessOptions(prompt="hi")
        with mock.patch(
            "src.deferred_init.start_deferred_prefetches"
        ) as kick, mock.patch.object(
            headless, "get_default_provider", side_effect=SystemExit(2)
        ):
            # SystemExit right after the kick point keeps the test cheap.
            with self.assertRaises(SystemExit):
                headless.run_headless(options)
            kick.assert_called_once()


if __name__ == "__main__":
    unittest.main()

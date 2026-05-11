"""Tests for ``src/bootstrap/state.py`` (Phase 1 expansion).

Verifies the chapter's two-tier architecture invariants on the
bootstrap-singleton side:

* Identity defaults are NFC-normalized at module load.
* ``session_id`` is a UUID (not strftime).
* ``switch_session`` is the only setter that mutates session_id + project_dir
  together (the CC-34 single-setter discipline).
* ``regenerate_session_id`` lineages parent correctly.
* ``mark_post_compaction`` / ``consume_post_compaction`` are one-shot.
* NFC normalization applies to every path setter.
* ``reset_state_for_tests`` is gated and resets cleanly.
* Existing accessors (``get_is_interactive`` etc.) still work.
"""

from __future__ import annotations

import asyncio
import os
import unicodedata
import unittest
from unittest import mock

import pytest

from src.bootstrap.state import (
    ModelUsage,
    SdkContext,
    SessionId,
    add_to_total_cost_state,
    add_to_total_duration_state,
    add_to_total_lines_changed,
    consume_post_compaction,
    get_cached_claude_md_content,
    get_client_type,
    get_cwd_state,
    get_is_interactive,
    get_is_non_interactive_session,
    get_main_loop_model_override,
    get_model_usage,
    get_original_cwd,
    get_parent_session_id,
    get_project_root,
    get_session_id,
    get_session_project_dir,
    get_total_api_duration,
    get_total_cost_usd,
    get_total_lines_added,
    has_unknown_model_cost,
    mark_post_compaction,
    on_session_switch,
    regenerate_session_id,
    reset_cost_state,
    reset_state_for_tests,
    run_with_sdk_context,
    set_cached_claude_md_content,
    set_client_type,
    set_cost_state_for_restore,
    set_cwd_state,
    set_has_unknown_model_cost,
    set_is_interactive,
    set_main_loop_model_override,
    set_original_cwd,
    set_project_root,
    switch_session,
)


@pytest.fixture(autouse=True)
def _reset_bootstrap_state():
    """Reset the bootstrap singleton before each test in this file.

    Without this, the global ``_STATE`` would leak between tests and a
    test that calls ``switch_session(...)`` would corrupt every following
    test. Autouse limits the reset to this module."""
    reset_state_for_tests()
    yield
    reset_state_for_tests()


class TestDefaults(unittest.TestCase):
    def test_session_id_is_uuid(self) -> None:
        sid = get_session_id()
        # UUID v4 string is 36 chars with hyphens at fixed positions
        self.assertIsInstance(sid, str)
        self.assertEqual(len(sid), 36)
        self.assertEqual(sid[8], "-")
        self.assertEqual(sid[13], "-")
        self.assertEqual(sid[18], "-")
        self.assertEqual(sid[23], "-")

    def test_parent_session_id_is_none_initially(self) -> None:
        self.assertIsNone(get_parent_session_id())

    def test_session_project_dir_is_none_initially(self) -> None:
        self.assertIsNone(get_session_project_dir())

    def test_paths_are_nfc_normalized(self) -> None:
        cwd = get_original_cwd()
        self.assertEqual(cwd, unicodedata.normalize("NFC", cwd))

    def test_project_root_equals_original_cwd_initially(self) -> None:
        self.assertEqual(get_original_cwd(), get_project_root())

    def test_cost_accumulators_start_at_zero(self) -> None:
        self.assertEqual(get_total_cost_usd(), 0.0)
        self.assertEqual(get_total_api_duration(), 0)
        self.assertEqual(get_total_lines_added(), 0)
        self.assertFalse(has_unknown_model_cost())
        self.assertEqual(get_model_usage(), {})

    def test_pending_post_compaction_starts_false(self) -> None:
        # Consume once: should be False because we never set it
        self.assertFalse(consume_post_compaction())

    def test_main_loop_model_override_is_none_initially(self) -> None:
        self.assertIsNone(get_main_loop_model_override())


class TestExistingAccessorsStillWork(unittest.TestCase):
    """The migration contract: get/set_is_interactive and get/set_client_type
    must continue to behave identically to the pre-expansion shape."""

    def test_is_interactive_default_false(self) -> None:
        self.assertFalse(get_is_interactive())
        self.assertTrue(get_is_non_interactive_session())

    def test_set_is_interactive_roundtrips(self) -> None:
        set_is_interactive(True)
        self.assertTrue(get_is_interactive())
        self.assertFalse(get_is_non_interactive_session())

    def test_client_type_default_and_setter(self) -> None:
        # Pre-existing default. TS uses 'cli'; we preserve the pre-expansion
        # value to keep migration backward compatible.
        self.assertEqual(get_client_type(), "claude-code")
        set_client_type("ide")
        self.assertEqual(get_client_type(), "ide")


class TestNfcNormalization(unittest.TestCase):
    """Every path setter must NFC-normalize input."""

    def test_set_original_cwd_normalizes_nfd_to_nfc(self) -> None:
        # NFD form of "é" is two code points: 'e' + combining acute
        nfd = "/path/café"  # "café" in NFD
        set_original_cwd(nfd)
        stored = get_original_cwd()
        self.assertEqual(stored, "/path/café")  # composed é
        self.assertEqual(stored, unicodedata.normalize("NFC", stored))

    def test_set_project_root_normalizes(self) -> None:
        nfd = "/proj/café"  # already NFC
        set_project_root(nfd)
        self.assertEqual(get_project_root(), nfd)
        # NFD input
        nfd2 = "/proj/café"
        set_project_root(nfd2)
        self.assertEqual(get_project_root(), "/proj/café")


class TestSwitchSession(unittest.TestCase):
    def test_switch_session_updates_id_and_project_dir(self) -> None:
        new_id = SessionId("11111111-1111-1111-1111-111111111111")
        switch_session(new_id, "/some/dir")
        self.assertEqual(get_session_id(), new_id)
        self.assertEqual(get_session_project_dir(), "/some/dir")

    def test_switch_session_with_none_project_dir(self) -> None:
        new_id = SessionId("22222222-2222-2222-2222-222222222222")
        switch_session(new_id)
        self.assertEqual(get_session_id(), new_id)
        self.assertIsNone(get_session_project_dir())

    def test_switch_session_emits_signal(self) -> None:
        received: list[SessionId] = []
        unsubscribe = on_session_switch(lambda sid: received.append(sid))
        try:
            new_id = SessionId("33333333-3333-3333-3333-333333333333")
            switch_session(new_id, "/x")
            self.assertEqual(received, [new_id])
        finally:
            unsubscribe()

    def test_switch_session_signal_fires_after_state_update(self) -> None:
        """The signal listener must see the new state via get_session_id()."""
        seen_ids: list[SessionId] = []

        unsubscribe = on_session_switch(lambda sid: seen_ids.append(get_session_id()))
        try:
            new_id = SessionId("44444444-4444-4444-4444-444444444444")
            switch_session(new_id)
            self.assertEqual(seen_ids, [new_id])
        finally:
            unsubscribe()


class TestRegenerateSessionId(unittest.TestCase):
    def test_regenerate_returns_new_uuid(self) -> None:
        old = get_session_id()
        new = regenerate_session_id()
        self.assertNotEqual(old, new)
        self.assertEqual(get_session_id(), new)

    def test_regenerate_with_parent_flag(self) -> None:
        old = get_session_id()
        regenerate_session_id(set_current_as_parent=True)
        self.assertEqual(get_parent_session_id(), old)

    def test_regenerate_without_parent_flag_leaves_parent_none(self) -> None:
        regenerate_session_id()
        self.assertIsNone(get_parent_session_id())

    def test_regenerate_clears_session_project_dir(self) -> None:
        # Set a project dir, then regenerate — should clear
        switch_session(SessionId("55555555-5555-5555-5555-555555555555"), "/old")
        self.assertEqual(get_session_project_dir(), "/old")
        regenerate_session_id()
        self.assertIsNone(get_session_project_dir())

    def test_regenerate_does_NOT_emit_signal(self) -> None:
        """regenerate is the /clear path; switch_session is the resume path.
        Only switch_session fires the signal — concurrentSessions/PID-file
        sync only cares about cross-process boundary changes."""
        received: list = []
        unsubscribe = on_session_switch(lambda sid: received.append(sid))
        try:
            regenerate_session_id()
            self.assertEqual(received, [])
        finally:
            unsubscribe()


class TestPostCompaction(unittest.TestCase):
    def test_pending_post_compaction_one_shot(self) -> None:
        mark_post_compaction()
        self.assertIs(consume_post_compaction(), True)
        self.assertIs(consume_post_compaction(), False)
        self.assertIs(consume_post_compaction(), False)

    def test_mark_is_idempotent(self) -> None:
        mark_post_compaction()
        mark_post_compaction()
        # Still only one consumption flips it
        self.assertIs(consume_post_compaction(), True)
        self.assertIs(consume_post_compaction(), False)


class TestCostState(unittest.TestCase):
    def test_add_to_total_cost_state_accumulates(self) -> None:
        usage1 = ModelUsage(input_tokens=100, output_tokens=50, cost_usd=0.5)
        usage2 = ModelUsage(input_tokens=200, output_tokens=100, cost_usd=1.0)

        add_to_total_cost_state(0.5, usage1, "claude-sonnet-4")
        add_to_total_cost_state(1.0, usage2, "claude-opus-4")

        self.assertEqual(get_total_cost_usd(), 1.5)
        self.assertEqual(get_model_usage()["claude-sonnet-4"].cost_usd, 0.5)
        self.assertEqual(get_model_usage()["claude-opus-4"].cost_usd, 1.0)

    def test_add_to_total_duration_state_accumulates(self) -> None:
        add_to_total_duration_state(100, 80)
        add_to_total_duration_state(200, 150)
        self.assertEqual(get_total_api_duration(), 300)

    def test_add_to_total_lines_changed_accumulates(self) -> None:
        add_to_total_lines_changed(10, 5)
        add_to_total_lines_changed(20, 15)
        self.assertEqual(get_total_lines_added(), 30)

    def test_has_unknown_model_cost_setter(self) -> None:
        self.assertFalse(has_unknown_model_cost())
        set_has_unknown_model_cost()
        self.assertTrue(has_unknown_model_cost())

    def test_reset_cost_state_wipes_accumulators(self) -> None:
        add_to_total_cost_state(2.5, ModelUsage(cost_usd=2.5), "claude-opus-4")
        add_to_total_lines_changed(50, 25)
        set_has_unknown_model_cost()

        reset_cost_state()

        self.assertEqual(get_total_cost_usd(), 0.0)
        self.assertEqual(get_total_lines_added(), 0)
        self.assertFalse(has_unknown_model_cost())
        self.assertEqual(get_model_usage(), {})

    def test_set_cost_state_for_restore(self) -> None:
        set_cost_state_for_restore(
            total_cost_usd=3.14,
            total_api_duration=1000,
            total_api_duration_without_retries=900,
            total_tool_duration=500,
            total_lines_added=42,
            total_lines_removed=21,
            model_usage={"claude-opus-4": ModelUsage(cost_usd=3.14)},
        )
        self.assertEqual(get_total_cost_usd(), 3.14)
        self.assertEqual(get_total_api_duration(), 1000)
        self.assertEqual(get_total_lines_added(), 42)
        self.assertEqual(get_model_usage()["claude-opus-4"].cost_usd, 3.14)


class TestModelOverride(unittest.TestCase):
    def test_set_main_loop_model_override(self) -> None:
        set_main_loop_model_override("claude-sonnet-4-6")
        self.assertEqual(get_main_loop_model_override(), "claude-sonnet-4-6")

    def test_clear_main_loop_model_override(self) -> None:
        set_main_loop_model_override("claude-sonnet-4-6")
        set_main_loop_model_override(None)
        self.assertIsNone(get_main_loop_model_override())


class TestCachedClaudeMd(unittest.TestCase):
    def test_cached_claude_md_starts_none(self) -> None:
        self.assertIsNone(get_cached_claude_md_content())

    def test_set_get_cached_claude_md(self) -> None:
        set_cached_claude_md_content("# Project Notes\nfoo")
        self.assertEqual(get_cached_claude_md_content(), "# Project Notes\nfoo")

    def test_clear_cached_claude_md(self) -> None:
        set_cached_claude_md_content("foo")
        set_cached_claude_md_content(None)
        self.assertIsNone(get_cached_claude_md_content())


class TestSdkContext(unittest.TestCase):
    """Per-query context overrides via ``run_with_sdk_context``.

    Mirrors the TS AsyncLocalStorage discipline at
    ``bootstrap/state.ts:438-608``: reads inside the context see context
    values; reads outside see the global. Mutations route accordingly.
    """

    def test_get_session_id_outside_context_returns_global(self) -> None:
        global_id = get_session_id()
        ctx = SdkContext(session_id=SessionId("ctx-id-12345"))
        with run_with_sdk_context(ctx):
            pass  # don't read inside
        # After the with block, we see the global again
        self.assertEqual(get_session_id(), global_id)

    def test_get_session_id_inside_context_returns_context(self) -> None:
        ctx_id = SessionId("ctx-id-aaaaa")
        ctx = SdkContext(session_id=ctx_id)
        with run_with_sdk_context(ctx):
            self.assertEqual(get_session_id(), ctx_id)

    def test_context_falls_back_to_global_for_unset_paths(self) -> None:
        """SdkContext with empty cwd/original_cwd falls back to the global."""
        global_cwd = get_original_cwd()
        ctx = SdkContext(session_id=SessionId("ctx-id-bbbbb"))
        with run_with_sdk_context(ctx):
            self.assertEqual(get_original_cwd(), global_cwd)

    def test_context_overrides_cwd_when_provided(self) -> None:
        ctx = SdkContext(
            session_id=SessionId("ctx-id-ccccc"),
            cwd="/tmp/sdk-workspace",
            original_cwd="/tmp/sdk-workspace",
        )
        with run_with_sdk_context(ctx):
            self.assertEqual(get_cwd_state(), "/tmp/sdk-workspace")
            self.assertEqual(get_original_cwd(), "/tmp/sdk-workspace")

    def test_switch_session_inside_context_mutates_context_not_global(self) -> None:
        global_id_before = get_session_id()
        ctx = SdkContext(session_id=SessionId("ctx-id-ddddd"))
        with run_with_sdk_context(ctx):
            switch_session(SessionId("new-id-inside"), "/some/dir")
            self.assertEqual(get_session_id(), "new-id-inside")
            self.assertEqual(get_session_project_dir(), "/some/dir")

        # Outside the with block, the global was NOT mutated
        self.assertEqual(get_session_id(), global_id_before)
        self.assertEqual(ctx.session_id, "new-id-inside")  # ctx WAS mutated

    def test_regenerate_session_id_inside_context_mutates_context(self) -> None:
        original_ctx_id = SessionId("ctx-id-eeeee")
        ctx = SdkContext(session_id=original_ctx_id)
        with run_with_sdk_context(ctx):
            new_id = regenerate_session_id(set_current_as_parent=True)
            self.assertEqual(get_session_id(), new_id)
            self.assertEqual(get_parent_session_id(), original_ctx_id)
            self.assertEqual(ctx.session_id, new_id)
            self.assertEqual(ctx.parent_session_id, original_ctx_id)

    def test_nested_contexts_isolated(self) -> None:
        """Nested ``run_with_sdk_context`` blocks isolate from each other."""
        outer_id = SessionId("outer-eeeee")
        inner_id = SessionId("inner-fffff")
        with run_with_sdk_context(SdkContext(session_id=outer_id)):
            self.assertEqual(get_session_id(), outer_id)
            with run_with_sdk_context(SdkContext(session_id=inner_id)):
                self.assertEqual(get_session_id(), inner_id)
            # After inner exits, we're back to outer
            self.assertEqual(get_session_id(), outer_id)

    def test_switch_session_inside_context_still_emits_signal(self) -> None:
        """The signal fires regardless of where the mutation happened."""
        received: list[SessionId] = []
        unsubscribe = on_session_switch(lambda sid: received.append(sid))
        try:
            ctx = SdkContext(session_id=SessionId("ctx-id-99999"))
            with run_with_sdk_context(ctx):
                new_id = SessionId("inside-switch")
                switch_session(new_id)
            self.assertEqual(received, [new_id])
        finally:
            unsubscribe()

    def test_set_original_cwd_inside_context_mutates_context(self) -> None:
        global_cwd_before = get_original_cwd()
        ctx = SdkContext(
            session_id=SessionId("ctx-id-77777"),
            original_cwd="/initial",
        )
        with run_with_sdk_context(ctx):
            set_original_cwd("/changed/inside")
            self.assertEqual(get_original_cwd(), "/changed/inside")
            self.assertEqual(ctx.original_cwd, "/changed/inside")

        # Global was NOT mutated
        self.assertEqual(get_original_cwd(), global_cwd_before)


class TestSdkContextAsyncIsolation(unittest.TestCase):
    """The whole point of ``contextvars.ContextVar`` over a regular module
    global: two concurrent ``asyncio`` tasks see independent context
    values. Without this property, the per-query isolation that M4 is
    supposed to provide silently breaks.

    Locks the property so a future "optimization" that replaces
    ``contextvars`` with a thread-local or module-global will fail
    these tests."""

    def test_concurrent_asyncio_tasks_see_isolated_session_ids(self) -> None:
        async def task(sid_value: str, results: list) -> None:
            ctx = SdkContext(session_id=SessionId(sid_value))
            with run_with_sdk_context(ctx):
                # Yield to let other tasks run mid-context. If contextvars
                # isn't isolating, this is where bleed would surface.
                await asyncio.sleep(0.01)
                observed_1 = get_session_id()
                await asyncio.sleep(0.01)
                observed_2 = get_session_id()
                results.append((sid_value, observed_1, observed_2))

        async def main() -> list:
            results: list = []
            await asyncio.gather(
                task("aaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", results),
                task("bbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", results),
                task("cccc-cccc-cccc-cccc-cccccccccccc", results),
            )
            return results

        results = asyncio.run(main())

        # Each task must see ONLY its own session_id — both before and
        # after the asyncio.sleep yield points
        for set_sid, obs_1, obs_2 in results:
            self.assertEqual(
                obs_1,
                set_sid,
                f"task {set_sid} saw {obs_1} on first read (contextvar bleed)",
            )
            self.assertEqual(
                obs_2,
                set_sid,
                f"task {set_sid} saw {obs_2} on second read (contextvar bleed)",
            )

    def test_concurrent_tasks_do_not_leak_to_outer_scope(self) -> None:
        """After concurrent tasks complete, the outer scope sees the
        global (no leak from any task's context)."""
        global_id_before = get_session_id()

        async def task(sid_value: str) -> None:
            with run_with_sdk_context(SdkContext(session_id=SessionId(sid_value))):
                await asyncio.sleep(0.01)

        async def main() -> None:
            await asyncio.gather(
                task("xxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"),
                task("yyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"),
            )

        asyncio.run(main())

        # Outer scope still sees the global
        self.assertEqual(get_session_id(), global_id_before)


class TestResetStateForTests(unittest.TestCase):
    def test_reset_outside_pytest_raises(self) -> None:
        """The gate prevents production code from accidentally wiping
        bootstrap state. Within pytest the autouse fixture above relies
        on PYTEST_CURRENT_TEST being set."""
        # Temporarily remove PYTEST_CURRENT_TEST to simulate production
        env = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            with self.assertRaises(RuntimeError):
                reset_state_for_tests()
        finally:
            if env is not None:
                os.environ["PYTEST_CURRENT_TEST"] = env

    def test_reset_clears_signal_subscribers(self) -> None:
        """Critical for test isolation: a subscriber from a previous test
        must not still be active in the next test."""
        from src.bootstrap.state import _session_switched

        on_session_switch(lambda sid: None)
        self.assertGreater(len(_session_switched._listeners), 0)

        reset_state_for_tests()

        self.assertEqual(len(_session_switched._listeners), 0)

    def test_reset_resets_cost_accumulators(self) -> None:
        add_to_total_cost_state(5.0, ModelUsage(cost_usd=5.0), "claude-opus")
        self.assertGreater(get_total_cost_usd(), 0)

        reset_state_for_tests()

        self.assertEqual(get_total_cost_usd(), 0.0)
        self.assertEqual(get_model_usage(), {})


if __name__ == "__main__":
    unittest.main()

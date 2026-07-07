"""Plan-mode port tests — plans/words, transitions, attachments, permissions.

Covers the seams the design review flagged as load-bearing
(my-docs/plan-mode/plan-mode-port-design.md §4):

* B2 pin — ``pre_plan_mode`` survives every ``apply_permission_update`` kind.
* Step-1e pin — ExitPlanMode still ASKS in bypassPermissions;
  EnterPlanMode auto-allows everywhere (incl. the headless implication).
* Plan-file write exemption — prefix+``.md`` semantics covering the
  subagent ``{slug}-agent-{id}.md`` variant.
* Attachment cadence — full on attachment #1, sparse #2..#5, full #6;
  ≥5 human-turn throttle; one-time re-entry; one-time exit.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from src.bootstrap.state import (
    get_plan_slug_cache,
    handle_plan_mode_transition,
    has_exited_plan_mode_in_session,
    needs_plan_mode_exit_attachment,
    set_has_exited_plan_mode,
    set_needs_plan_mode_exit_attachment,
)
from src.context_system.plan_mode import (
    build_plan_mode_attachments,
    build_plan_mode_exit_attachment,
    wrap_in_system_reminder,
)
from src.permissions.plan_transitions import (
    prepare_context_for_plan_mode,
    transition_permission_mode,
)
from src.permissions.types import (
    PermissionRuleValue,
    PermissionUpdateAddDirectories,
    PermissionUpdateAddRules,
    PermissionUpdateSetMode,
    ToolPermissionContext,
)
from src.permissions.updates import apply_permission_update
from src.utils import plans
from src.utils.words import ADJECTIVES, NOUNS, VERBS, generate_word_slug


class _FakeUserMsg:
    def __init__(self, content, role="user"):
        self.role = role
        self.content = content


def _human(text="hi"):
    return _FakeUserMsg(text)


def _tool_result_msg():
    return _FakeUserMsg([{"type": "tool_result", "tool_use_id": "t", "content": "ok"}])


def _attachment_msg(text):
    return _FakeUserMsg(wrap_in_system_reminder(text))


def _reset_plan_state():
    set_has_exited_plan_mode(False)
    set_needs_plan_mode_exit_attachment(False)
    get_plan_slug_cache().clear()


class TestWordsAndPlans(unittest.TestCase):
    def setUp(self):
        _reset_plan_state()

    def tearDown(self):
        try:
            plans.get_plan_file_path().unlink(missing_ok=True)
        except OSError:
            pass
        _reset_plan_state()

    def test_word_lists_match_reference_counts(self):
        # Transcribed 1:1 from typescript/src/utils/words.ts (737 words).
        self.assertEqual(len(ADJECTIVES) + len(NOUNS) + len(VERBS), 737)

    def test_slug_shape_adjective_verb_noun(self):
        slug = generate_word_slug()
        a, v, n = slug.split("-")
        self.assertIn(a, ADJECTIVES)
        self.assertIn(v, VERBS)
        self.assertIn(n, NOUNS)

    def test_slug_cached_per_session_and_clearable(self):
        p1 = plans.get_plan_file_path()
        self.assertEqual(p1, plans.get_plan_file_path())
        plans.clear_all_plan_slugs()
        # New slug is *very likely* different; assert the cache actually
        # emptied rather than relying on randomness.
        self.assertEqual(len(get_plan_slug_cache()), 0)

    def test_agent_plan_path_variant(self):
        main = plans.get_plan_file_path()
        agent = plans.get_plan_file_path("abc42")
        self.assertEqual(agent.name, f"{main.stem}-agent-abc42.md")

    def test_is_session_plan_file_prefix_and_suffix(self):
        main = plans.get_plan_file_path()
        agent = plans.get_plan_file_path("abc42")
        self.assertTrue(plans.is_session_plan_file(str(main)))
        self.assertTrue(plans.is_session_plan_file(str(agent)))
        self.assertFalse(plans.is_session_plan_file(str(main) + ".txt"))
        self.assertFalse(
            plans.is_session_plan_file(str(main.parent / "other-slug.md"))
        )

    def test_get_plan_none_then_content(self):
        self.assertIsNone(plans.get_plan())
        p = plans.get_plan_file_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# body", encoding="utf-8")
        self.assertEqual(plans.get_plan(), "# body")


class TestPrePlanModePreservation(unittest.TestCase):
    """B2 pin — apply_permission_update must carry pre_plan_mode through."""

    def test_survives_all_update_kinds(self):
        ctx = ToolPermissionContext(pre_plan_mode="acceptEdits")
        ctx = apply_permission_update(
            ctx, PermissionUpdateSetMode(type="setMode", destination="session", mode="plan")
        )
        self.assertEqual(ctx.pre_plan_mode, "acceptEdits")
        ctx = apply_permission_update(
            ctx,
            PermissionUpdateAddRules(
                type="addRules",
                destination="session",
                behavior="allow",
                rules=(PermissionRuleValue(tool_name="Bash", rule_content="ls:*"),),
            ),
        )
        self.assertEqual(ctx.pre_plan_mode, "acceptEdits")
        ctx = apply_permission_update(
            ctx,
            PermissionUpdateAddDirectories(
                type="addDirectories", destination="session", directories=("/tmp/x",)
            ),
        )
        self.assertEqual(ctx.pre_plan_mode, "acceptEdits")


class TestPlanTransitions(unittest.TestCase):
    def setUp(self):
        _reset_plan_state()

    def tearDown(self):
        _reset_plan_state()

    def test_prepare_stashes_and_noops_in_plan(self):
        ctx = ToolPermissionContext(mode="acceptEdits")
        out = prepare_context_for_plan_mode(ctx)
        self.assertEqual(out.pre_plan_mode, "acceptEdits")
        already = ToolPermissionContext(mode="plan", pre_plan_mode="default")
        self.assertIs(prepare_context_for_plan_mode(already), already)

    def test_same_mode_is_noop(self):
        set_needs_plan_mode_exit_attachment(False)
        ctx = ToolPermissionContext(mode="plan")
        out = transition_permission_mode("plan", "plan", ctx)
        self.assertIs(out, ctx)
        self.assertFalse(needs_plan_mode_exit_attachment())

    def test_leaving_plan_sets_flags_and_clears_stash(self):
        ctx = ToolPermissionContext(mode="plan", pre_plan_mode="acceptEdits")
        out = transition_permission_mode("plan", "default", ctx)
        self.assertIsNone(out.pre_plan_mode)
        self.assertTrue(needs_plan_mode_exit_attachment())
        self.assertTrue(has_exited_plan_mode_in_session())

    def test_entering_plan_stashes_and_clears_pending_exit(self):
        set_needs_plan_mode_exit_attachment(True)
        ctx = ToolPermissionContext(mode="default")
        out = transition_permission_mode("default", "plan", ctx)
        self.assertEqual(out.pre_plan_mode, "default")
        # quick toggle: plan_mode_exit must not fire after re-entry
        self.assertFalse(needs_plan_mode_exit_attachment())

    def test_handle_plan_mode_transition_matrix(self):
        set_needs_plan_mode_exit_attachment(False)
        handle_plan_mode_transition("plan", "acceptEdits")
        self.assertTrue(needs_plan_mode_exit_attachment())
        handle_plan_mode_transition("acceptEdits", "plan")
        self.assertFalse(needs_plan_mode_exit_attachment())


class TestPlanModeAttachments(unittest.TestCase):
    def setUp(self):
        _reset_plan_state()

    def tearDown(self):
        try:
            plans.get_plan_file_path().unlink(missing_ok=True)
        except OSError:
            pass
        _reset_plan_state()

    def test_not_in_plan_mode_returns_nothing(self):
        self.assertEqual(build_plan_mode_attachments([_human()], "default"), [])

    def test_first_turn_full_then_throttled(self):
        texts = build_plan_mode_attachments([_human()], "plan")
        self.assertEqual(len(texts), 1)
        self.assertIn("Plan mode is active", texts[0])
        self.assertIn("### Phase 5: Call ExitPlanMode", texts[0])

        # Simulate the persisted conversation: attachment + <5 human turns.
        messages = [_human(), _attachment_msg(texts[0])]
        for _ in range(4):
            messages.append(_human())
            self.assertEqual(build_plan_mode_attachments(messages, "plan"), [])

        # 5th human turn since the attachment → re-attach (sparse, #2).
        messages.append(_human())
        texts2 = build_plan_mode_attachments(messages, "plan")
        self.assertEqual(len(texts2), 1)
        self.assertIn("Plan mode still active", texts2[0])

    def test_tool_result_turns_do_not_count(self):
        texts = build_plan_mode_attachments([_human()], "plan")
        messages = [_human(), _attachment_msg(texts[0])]
        for _ in range(10):
            messages.append(_tool_result_msg())
        self.assertEqual(build_plan_mode_attachments(messages, "plan"), [])

    def test_full_sparse_cycle_full_on_sixth_attachment(self):
        # Build a history holding N prior attachments; the (N+1)th's flavor
        # follows attachment_count % 5 == 1 → full (1st, 6th, 11th …).
        def history(n_attachments):
            msgs = []
            first = True
            for _ in range(n_attachments):
                body = (
                    "Plan mode is active. …" if first else "Plan mode still active …"
                )
                first = False
                msgs.append(_attachment_msg(body))
                for _ in range(5):
                    msgs.append(_human())
            return msgs

        # Attachment #2 (turn ~6) is SPARSE (critic note: NOT full).
        t2 = build_plan_mode_attachments(history(1), "plan")
        self.assertIn("Plan mode still active", t2[0])
        # Attachment #6 is FULL again.
        t6 = build_plan_mode_attachments(history(5), "plan")
        self.assertIn("Plan mode is active", t6[0])

    def test_reentry_precedes_full_when_exited_and_plan_exists(self):
        p = plans.get_plan_file_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# old plan", encoding="utf-8")
        set_has_exited_plan_mode(True)

        texts = build_plan_mode_attachments([_human()], "plan")
        self.assertEqual(len(texts), 2)
        self.assertIn("Re-entering Plan Mode", texts[0])
        self.assertIn("Plan mode is active", texts[1])
        # One-time: the flag cleared.
        self.assertFalse(has_exited_plan_mode_in_session())

    def test_no_reentry_without_plan_file(self):
        set_has_exited_plan_mode(True)
        texts = build_plan_mode_attachments([_human()], "plan")
        self.assertEqual(len(texts), 1)
        self.assertIn("Plan mode is active", texts[0])
        # Flag NOT consumed (TS clears it only when the reentry fires).
        self.assertTrue(has_exited_plan_mode_in_session())

    def test_subagent_variant(self):
        texts = build_plan_mode_attachments([_human()], "plan", agent_id="a1")
        self.assertEqual(len(texts), 1)
        self.assertIn("-agent-a1.md", texts[0])
        self.assertIn("Answer the user's query comprehensively", texts[0])

    def test_exit_attachment_one_shot(self):
        set_needs_plan_mode_exit_attachment(True)
        texts = build_plan_mode_exit_attachment("default")
        self.assertEqual(len(texts), 1)
        self.assertIn("## Exited Plan Mode", texts[0])
        self.assertEqual(build_plan_mode_exit_attachment("default"), [])

    def test_exit_attachment_suppressed_in_plan_mode(self):
        set_needs_plan_mode_exit_attachment(True)
        self.assertEqual(build_plan_mode_exit_attachment("plan"), [])
        # Consumed either way (TS clears the flag on both branches).
        self.assertFalse(needs_plan_mode_exit_attachment())


class TestPlanPermissions(unittest.TestCase):
    def setUp(self):
        _reset_plan_state()

    def tearDown(self):
        try:
            plans.get_plan_file_path().unlink(missing_ok=True)
        except OSError:
            pass
        _reset_plan_state()

    def test_exit_plan_mode_asks_even_in_bypass(self):
        # Step-1e pin (check.py:416-426): the requires_user_interaction ask
        # must survive bypassPermissions — the plan approval is the gate.
        from src.permissions.check import has_permissions_to_use_tool
        from src.tool_system.tools.plan_mode import ExitPlanModeTool

        ctx = ToolPermissionContext(
            mode="bypassPermissions", is_bypass_permissions_mode_available=True
        )
        decision = has_permissions_to_use_tool(ExitPlanModeTool, {}, ctx)
        self.assertEqual(decision.behavior, "ask")
        self.assertEqual(decision.message, "Exit plan mode?")

    def test_enter_plan_mode_auto_allows(self):
        # TS Tool.ts default-allow parity: no entry dialog, and no headless
        # deny for proactive plan entry.
        from src.permissions.check import has_permissions_to_use_tool
        from src.tool_system.tools.plan_mode import EnterPlanModeTool

        for mode in ("default", "acceptEdits", "plan", "bypassPermissions"):
            ctx = ToolPermissionContext(mode=mode)  # type: ignore[arg-type]
            decision = has_permissions_to_use_tool(EnterPlanModeTool, {}, ctx)
            self.assertEqual(decision.behavior, "allow", mode)

    def test_plan_file_write_exemption_main_and_agent(self):
        from src.permissions.check import has_permissions_to_use_tool
        from src.tool_system.tools.edit import EditTool

        ctx = ToolPermissionContext(mode="default")
        for path in (
            str(plans.get_plan_file_path()),
            str(plans.get_plan_file_path("sub7")),
        ):
            decision = has_permissions_to_use_tool(
                EditTool,
                {"file_path": path, "old_string": "", "new_string": "x"},
                ctx,
            )
            self.assertEqual(decision.behavior, "allow", path)
            self.assertIn("Plan files", decision.decision_reason.reason)

    def test_plan_file_write_passes_tool_containment(self):
        # e2e-found bug class: the PERMISSION exemption allowed the plan-file
        # write but the Edit/Write TOOL's own workspace-containment gate
        # (ToolContext.ensure_allowed_path) rejected the plans dir — TS's
        # checkEditableInternalPath exempts plan files BEFORE the working-dir
        # gate, so the port's containment layer must carve them out too.
        import tempfile
        from pathlib import Path

        from src.tool_system.context import ToolContext
        from src.tool_system.tools.edit import EditTool

        with tempfile.TemporaryDirectory() as td:
            ctx = ToolContext(workspace_root=Path(td))
            ctx.permission_context.mode = "plan"
            plan_path = plans.get_plan_file_path()
            result = EditTool.call(
                {"file_path": str(plan_path), "old_string": "", "new_string": "# p"},
                ctx,
            )
            self.assertFalse(result.is_error)
            self.assertTrue(plan_path.exists())
            plan_path.unlink()

            # The carve-out is EXACT: other files in the plans dir and
            # arbitrary outside paths still refuse.
            from src.tool_system.errors import ToolPermissionError

            with self.assertRaises(ToolPermissionError):
                EditTool.call(
                    {
                        "file_path": str(plan_path.parent / "other-sess.md"),
                        "old_string": "",
                        "new_string": "x",
                    },
                    ctx,
                )

    def test_non_plan_writes_still_ask(self):
        from src.permissions.check import has_permissions_to_use_tool
        from src.tool_system.tools.edit import EditTool

        ctx = ToolPermissionContext(mode="default")
        sibling = str(plans.get_plan_file_path().parent / "unrelated.md")
        decision = has_permissions_to_use_tool(
            EditTool,
            {"file_path": sibling, "old_string": "", "new_string": "x"},
            ctx,
        )
        self.assertEqual(decision.behavior, "ask")


if __name__ == "__main__":
    unittest.main()

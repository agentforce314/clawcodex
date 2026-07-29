"""--dangerously-skip-permissions wiring through the TUI/agent-server path.

The headless path was already covered (tests/test_dangerous_skip_permissions.py);
these tests pin the interactive chain that used to drop the flags:

- `clawcodex agent-server --dangerously-skip-permissions` forces
  bypassPermissions mode; `--allow-dangerously-skip-permissions` makes bypass
  AVAILABLE without entering it (AgentServerConfig.is_bypass_available).
- `_build_runtime` derives permission-context availability from
  mode-implies-available OR the forwarded `cfg.is_bypass_available` — it does
  NOT read settings ambiently (availability is resolved once per launch and
  carried in). Mirrors typescript/src/utils/permissions/permissionSetup.ts:941.
- The `set_permission_mode` control validates the mode and gates
  bypassPermissions on availability — the same guard the Shift+Tab cycle
  enforces (mirrors the onSetPermissionMode contract in
  typescript/src/bridge/replBridge.ts:182-193).
- `has_allow_bypass_permissions_mode()` reads the user + local settings
  tiers only, EXCLUDING the committable project tier (security parity with
  hasAllowBypassPermissionsMode in typescript/.../settings.ts:897).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncio

import pytest

from src.bootstrap.state import reset_state_for_tests
from src.services.startup_gates import reset_session_trust_for_testing

pytestmark = pytest.mark.integration


def _reset_all() -> None:
    reset_state_for_tests()
    reset_session_trust_for_testing()
    from src.state.app_state import set_active_provider_supplier

    set_active_provider_supplier(None)


class _SessionHarness(unittest.TestCase):
    """Real `_AgentSession` runtime against the keyless `ollama` provider with
    global config redirected to a temp dir (same shape as the ch03 harness)."""

    def setUp(self) -> None:
        _reset_all()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.ws = root / "ws"
        self.ws.mkdir()
        self.config_dir = root / "config-home"
        self.config_dir.mkdir()
        global_path = self.config_dir / "config.json"
        global_path.write_text(json.dumps({}), encoding="utf-8")
        self._patches = [
            patch("src.config.get_global_config_path", return_value=global_path),
            patch("src.config.GLOBAL_CONFIG_DIR", str(self.config_dir)),
        ]
        for p in self._patches:
            p.start()
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()
        _reset_all()
        self._tmp.cleanup()

    def _build(self, **cfg_kwargs):
        from src.server.agent_server import (
            AgentServerConfig,
            _AgentSession,
            _build_runtime,
        )

        sess = _AgentSession(
            session_id="s-bypass",
            cwd=str(self.ws),
            config=AgentServerConfig(
                provider_name="ollama",
                single_session=True,
                **cfg_kwargs,
            ),
            loop=MagicMock(),
            out_queue=MagicMock(),
        )
        _build_runtime(sess, None)
        self.assertIsNone(sess.init_error, f"runtime build failed: {sess.init_error}")
        return sess

    @staticmethod
    def _control_replies(sess) -> list[dict]:
        """Control responses captured through the mocked loop/_emit path."""
        out = []
        for call in sess.loop.call_soon_threadsafe.call_args_list:
            args = call.args
            if (
                len(args) == 2
                and isinstance(args[1], dict)
                and args[1].get("type") == "control_response"
            ):
                out.append(args[1]["response"]["response"])
        return out

    def _set_mode(self, sess, mode) -> dict:
        asyncio.run(sess._handle_control_request({
            "request_id": "r-mode",
            "request": {"subtype": "set_permission_mode", "mode": mode},
        }))
        replies = self._control_replies(sess)
        self.assertTrue(replies, "set_permission_mode sent no control_response")
        return replies[-1]


class TestBuildRuntimeAvailability(_SessionHarness):
    def test_default_session_has_no_bypass_availability(self) -> None:
        sess = self._build()
        pc = sess.tool_context.permission_context
        self.assertEqual(pc.mode, "default")
        self.assertFalse(pc.is_bypass_permissions_mode_available)

    def test_launching_in_bypass_mode_does_not_imply_availability(self) -> None:
        """Launching IN bypass must NOT set the engine availability flag.

        _build_runtime used to OR the launch mode into availability. That flag
        also relaxes **plan mode** (``check.py`` ``should_bypass``: ``mode ==
        "plan" and is_bypass_permissions_mode_available``), so once Full Access
        became the interactive default, every session's ``/plan`` silently
        permitted every edit and command — the exact opposite of what plan mode
        is for.

        Availability is now flag/settings-derived only (matching TS
        ``isBypassPermissionsModeAvailable`` and the headless path, which never
        had a mode-implies-availability rule). A session still bypasses while IN
        bypass mode via the engine's own ``mode == "bypassPermissions"`` clause;
        this test is the pin that stops the clause being re-added.
        """
        sess = self._build(permission_mode="bypassPermissions")
        pc = sess.tool_context.permission_context
        self.assertEqual(pc.mode, "bypassPermissions")
        self.assertFalse(pc.is_bypass_permissions_mode_available)

    def test_plan_mode_still_asks_in_an_implicit_full_access_session(self) -> None:
        """The regression the availability split exists to prevent."""
        import dataclasses

        from src.permissions.check import has_permissions_to_use_tool
        from src.tool_system.tools.write import WriteTool

        sess = self._build(permission_mode="bypassPermissions")
        pc = sess.tool_context.permission_context
        args = {"file_path": str(self.ws / "x.txt"), "content": "x"}

        # Full Access: the write is allowed outright.
        self.assertEqual(has_permissions_to_use_tool(WriteTool, args, pc).behavior, "allow")

        # …but plan mode must restrain it rather than inherit the bypass, which
        # is exactly what is_bypass_permissions_mode_available=True would have
        # made it do (check.py `should_bypass`).
        plan_pc = dataclasses.replace(pc, mode="plan")
        self.assertNotEqual(has_permissions_to_use_tool(WriteTool, args, plan_pc).behavior, "allow")

    def test_allow_flag_grants_availability_without_entering_bypass(self) -> None:
        sess = self._build(is_bypass_available=True)
        pc = sess.tool_context.permission_context
        self.assertEqual(pc.mode, "default")
        self.assertTrue(pc.is_bypass_permissions_mode_available)

    def test_shift_tab_cycle_reaches_bypass_only_when_available(self) -> None:
        from src.permissions.cycle import get_next_permission_mode

        available = self._build(is_bypass_available=True)
        available.tool_context.permission_context.mode = "plan"
        self.assertEqual(
            get_next_permission_mode(available.tool_context.permission_context),
            "bypassPermissions",
        )

        unavailable = self._build()
        unavailable.tool_context.permission_context.mode = "plan"
        self.assertEqual(
            get_next_permission_mode(unavailable.tool_context.permission_context),
            "default",
        )


class TestSetPermissionModeGate(_SessionHarness):
    def test_rejects_bypass_when_unavailable(self) -> None:
        sess = self._build()
        reply = self._set_mode(sess, "bypassPermissions")
        self.assertIs(reply.get("ok"), False)
        self.assertIn("not available", reply.get("error", ""))
        self.assertEqual(sess.tool_context.permission_context.mode, "default")

    def test_allows_bypass_when_available(self) -> None:
        sess = self._build(is_bypass_available=True)
        reply = self._set_mode(sess, "bypassPermissions")
        self.assertIs(reply.get("ok"), True)
        self.assertEqual(reply.get("mode"), "bypassPermissions")
        self.assertEqual(
            sess.tool_context.permission_context.mode, "bypassPermissions",
        )

    def test_rejects_unknown_mode_string(self) -> None:
        sess = self._build()
        reply = self._set_mode(sess, "banana")
        self.assertIs(reply.get("ok"), False)
        self.assertEqual(sess.tool_context.permission_context.mode, "default")

    def test_rejects_bubble_as_top_level_mode(self) -> None:
        sess = self._build()
        reply = self._set_mode(sess, "bubble")
        self.assertIs(reply.get("ok"), False)
        self.assertEqual(sess.tool_context.permission_context.mode, "default")

    def test_plain_modes_still_settable(self) -> None:
        sess = self._build()
        reply = self._set_mode(sess, "acceptEdits")
        self.assertIs(reply.get("ok"), True)
        self.assertEqual(sess.tool_context.permission_context.mode, "acceptEdits")

    def test_selectable_allows_bypass_without_granting_engine_availability(self) -> None:
        """`/permissions full` must work in an interactive session — but choosing
        it sets the MODE only. Flipping engine availability too would relax plan
        mode for the rest of the session."""
        sess = self._build(bypass_selectable=True)
        reply = self._set_mode(sess, "bypassPermissions")
        self.assertIs(reply.get("ok"), True)
        pc = sess.tool_context.permission_context
        self.assertEqual(pc.mode, "bypassPermissions")
        self.assertFalse(pc.is_bypass_permissions_mode_available)

    def _set_mode_persist(self, sess, mode) -> dict:
        asyncio.run(sess._handle_control_request({
            "request_id": f"r-persist-{mode}",
            "request": {
                "subtype": "set_permission_mode", "mode": mode, "persist": True,
            },
        }))
        return self._control_replies(sess)[-1]

    def test_persist_writes_default_mode_only_when_asked(self) -> None:
        """Picking a level is a standing preference; Shift+Tab is not."""
        from src.permissions.modes import read_settings_default_mode

        # bypass_selectable marks a session an interactive launcher owns, which
        # is what licenses a host-wide write.
        sess = self._build(bypass_selectable=True)

        self._set_mode(sess, "acceptEdits")  # no persist flag
        self.assertIsNone(read_settings_default_mode(str(self.ws)))

        reply = self._set_mode_persist(sess, "default")
        self.assertIs(reply.get("ok"), True)
        self.assertIs(reply.get("persisted"), True)
        self.assertEqual(read_settings_default_mode(str(self.ws)), "default")

    def test_persist_refused_for_a_session_no_interactive_client_owns(self) -> None:
        """Persisting is HOST-WIDE and durable — it is read at every future
        launch, in every project, including headless. A --print-connect / --http
        client must not be able to overwrite a user who deliberately chose "Ask
        for approval", or install a durable `dontAsk` they have to find in a
        settings file to undo."""
        from src.permissions.modes import read_settings_default_mode

        sess = self._build()  # bypass_selectable=False

        reply = self._set_mode_persist(sess, "acceptEdits")
        self.assertIs(reply.get("ok"), True)  # the MODE change still applies…
        self.assertEqual(sess.tool_context.permission_context.mode, "acceptEdits")
        self.assertIs(reply.get("persisted"), False)  # …but nothing was written
        self.assertIsNone(read_settings_default_mode(str(self.ws)))

    def test_persist_refuses_a_mode_the_reader_would_ignore(self) -> None:
        """`auto` is settable but is not an EXTERNAL_PERMISSION_MODE, so
        read_settings_default_mode drops it — persisting it would clobber a real
        prior choice with a value nothing reads back."""
        from src.permissions.modes import read_settings_default_mode, set_settings_default_mode

        sess = self._build(bypass_selectable=True)
        set_settings_default_mode("default")

        reply = self._set_mode_persist(sess, "auto")
        self.assertIs(reply.get("persisted"), False)
        self.assertEqual(read_settings_default_mode(str(self.ws)), "default")


class TestChosenUpdatesGate(_SessionHarness):
    """`chosen_updates` is a SECOND door to the permission mode.

    It rides on a permission-ask REPLY (the plan dialog's "Yes, and bypass
    permissions" arm), so it never passed through the `set_permission_mode`
    gate. A client could answer any prompt with a `setMode` and take Full Access
    in a session where it is not available — and with a persisted destination,
    write `permissions.defaultMode` into the HOST's settings file, which is now
    read at every launch.
    """

    def _reply_with_setmode(self, sess, mode, destination="session"):
        return sess._permission_reply({
            "behavior": "allow",
            "chosen_updates": [
                {"type": "setMode", "destination": destination, "mode": mode},
            ],
        })

    def test_wire_setmode_bypass_refused_without_the_capability(self) -> None:
        sess = self._build()
        self.assertEqual(self._reply_with_setmode(sess, "bypassPermissions").chosen_updates, ())

    def test_wire_setmode_bypass_allowed_with_selectability(self) -> None:
        sess = self._build(bypass_selectable=True)
        chosen = self._reply_with_setmode(sess, "bypassPermissions").chosen_updates
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0].mode, "bypassPermissions")

    def test_wire_setmode_to_a_persisted_destination_is_refused(self) -> None:
        # A mode must not become a permanent host-wide setting because a client
        # asked for it in a prompt reply. Same policy as the set_permission_mode
        # `persist` flag — one `_may_persist_mode`, both doors.
        sess = self._build()
        self.assertEqual(
            self._reply_with_setmode(sess, "acceptEdits", "userSettings").chosen_updates, (),
        )

    def test_rule_grants_are_untouched(self) -> None:
        # The gate must not break the "always allow Bash(ls:*)" channel, which
        # is what chosen_updates exists for.
        sess = self._build()
        reply = sess._permission_reply({
            "behavior": "allow",
            "chosen_updates": [{
                "type": "addRules",
                "destination": "session",
                "behavior": "allow",
                "rules": [{"toolName": "Bash", "ruleContent": "ls:*"}],
            }],
        })
        self.assertEqual(len(reply.chosen_updates), 1)


class TestShiftTabCycle(_SessionHarness):
    def test_cycle_can_return_to_full_access_when_selectable(self) -> None:
        """A session that DEFAULTS to Full Access leaves engine availability
        False on purpose, so the cycle needs selectability too — otherwise the
        first Shift+Tab was a one-way exit out of the default mode while the
        footer kept advertising "(shift+tab to cycle)"."""
        from src.permissions.cycle import get_next_permission_mode

        sess = self._build(permission_mode="bypassPermissions", bypass_selectable=True)
        pc = sess.tool_context.permission_context
        pc.mode = "plan"
        self.assertEqual(
            get_next_permission_mode(pc, can_select_bypass=sess.config.bypass_selectable),
            "bypassPermissions",
        )

    def test_cycle_still_skips_bypass_without_either_capability(self) -> None:
        from src.permissions.cycle import get_next_permission_mode

        sess = self._build()
        pc = sess.tool_context.permission_context
        pc.mode = "plan"
        self.assertEqual(
            get_next_permission_mode(pc, can_select_bypass=sess.config.bypass_selectable),
            "default",
        )


class TestPlanApprovalBypassArm(_SessionHarness):
    """Regression: `/plan` must not be a one-way exit out of Full Access.

    The ExitPlanMode approval box picks its elevated option from the wire's
    `bypass_available` (prompts.tsx `planApprovalOptions`). When that read only
    the ENGINE availability flag — which an implicit-full-access session
    deliberately leaves False — the box offered only "Yes, auto-accept edits",
    whose `chosen_updates` setMode pre-empts ExitPlanMode's `pre_plan_mode`
    restore. Approving a plan silently downgraded the session with no notice.
    """

    def _exit_plan_wire(self, sess) -> dict:
        from src.permissions.types import PermissionAskRequest

        sess.config.permission_timeout_s = 0.05  # no client will answer
        sess.permission_handler(
            PermissionAskRequest(
                tool_name="ExitPlanMode", message="Exit plan mode?", tool_input={},
            ),
        )
        for call in sess.loop.call_soon_threadsafe.call_args_list:
            args = call.args
            if (
                len(args) == 2
                and isinstance(args[1], dict)
                and args[1].get("type") == "control_request"
                and args[1]["request"].get("tool_name") == "ExitPlanMode"
            ):
                return args[1]["request"]
        self.fail("no ExitPlanMode control_request was emitted")

    def test_selectable_session_is_offered_the_bypass_arm(self) -> None:
        sess = self._build(permission_mode="bypassPermissions", bypass_selectable=True)
        self.assertTrue(self._exit_plan_wire(sess)["bypass_available"])

    def test_plain_session_is_not(self) -> None:
        sess = self._build()
        self.assertFalse(self._exit_plan_wire(sess)["bypass_available"])


class TestAgentServerCliFlags(unittest.TestCase):
    """The subcommand flags land in AgentServerConfig before serving."""

    def _run(self, argv: list[str], *, settings_bypass: bool = False):
        import src.entrypoints.agent_server_cli as cli

        captured: dict = {}

        async def fake_serve(args, workspace, agent_config):
            captured["cfg"] = agent_config
            return 0

        async def fake_serve_stdio(workspace, agent_config):
            captured["cfg"] = agent_config
            return 0

        with patch.object(cli, "_serve", fake_serve), \
                patch.object(cli, "_serve_stdio", fake_serve_stdio), \
                patch(
                    "src.permissions.modes.has_allow_bypass_permissions_mode",
                    return_value=settings_bypass,
                ):
            rc = cli.run_agent_server_subcommand(argv)
        self.assertEqual(rc, 0)
        return captured["cfg"]

    def test_dsp_flag_forces_bypass_mode_and_availability(self) -> None:
        cfg = self._run(["--dangerously-skip-permissions"])
        self.assertEqual(cfg.permission_mode, "bypassPermissions")
        self.assertTrue(cfg.is_bypass_available)

    def test_dsp_flag_wins_over_permission_mode(self) -> None:
        cfg = self._run([
            "--permission-mode", "plan", "--dangerously-skip-permissions",
        ])
        self.assertEqual(cfg.permission_mode, "bypassPermissions")

    def test_allow_flag_grants_availability_only(self) -> None:
        cfg = self._run(["--allow-dangerously-skip-permissions"])
        self.assertEqual(cfg.permission_mode, "default")
        self.assertTrue(cfg.is_bypass_available)

    def test_no_flags_no_availability(self) -> None:
        cfg = self._run([])
        self.assertEqual(cfg.permission_mode, "default")
        self.assertFalse(cfg.is_bypass_available)
        # The child is a CARRIER, not a resolver: no implicit full-access floor
        # here. That is decided once at the interactive launch boundary and
        # forwarded, so a directly-launched agent-server (the VS Code extension)
        # is unaffected by the loose interactive default.
        self.assertFalse(cfg.bypass_selectable)

    def test_allow_select_bypass_grants_selectability_only(self) -> None:
        """`--allow-select-bypass` lets `/permissions` choose Full Access without
        granting engine bypass availability — that one also relaxes plan mode."""
        cfg = self._run(["--allow-select-bypass"])
        self.assertTrue(cfg.bypass_selectable)
        self.assertFalse(cfg.is_bypass_available)
        self.assertEqual(cfg.permission_mode, "default")

    def test_availability_implies_selectability(self) -> None:
        cfg = self._run(["--allow-dangerously-skip-permissions"])
        self.assertTrue(cfg.is_bypass_available)
        self.assertTrue(cfg.bypass_selectable)

    def test_lockdown_revokes_selectability(self) -> None:
        with patch(
            "src.permissions.modes.is_bypass_permissions_mode_disabled",
            return_value=True,
        ):
            cfg = self._run(["--allow-select-bypass"])
        self.assertFalse(cfg.bypass_selectable)
        self.assertFalse(cfg.is_bypass_available)

    def test_no_flags_defaults_max_turns_to_shared_constant(self) -> None:
        # Pins the --max-turns CLI default to the same DEFAULT_MAX_TURNS the
        # AgentServerConfig dataclass field uses, so the two can't silently
        # drift apart again (they used to be two independently hand-edited
        # literals with nothing to catch a partial edit).
        from src.server.agent_server import DEFAULT_MAX_TURNS

        cfg = self._run([])
        self.assertEqual(cfg.max_turns, DEFAULT_MAX_TURNS)

    def test_stdio_folds_in_settings_availability(self) -> None:
        # A hand-launched single-session stdio server honors the operator's
        # own user/local settings.allowBypassPermissionsMode.
        cfg = self._run(["--stdio"], settings_bypass=True)
        self.assertEqual(cfg.permission_mode, "default")
        self.assertTrue(cfg.is_bypass_available)

    def test_http_does_not_fold_in_settings_availability(self) -> None:
        # The multi-session --http transport must NOT let the host's settings
        # unlock bypass for every remote client session.
        cfg = self._run([], settings_bypass=True)
        self.assertFalse(cfg.is_bypass_available)


if __name__ == "__main__":
    unittest.main()

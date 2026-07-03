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

    def test_launching_in_bypass_mode_implies_availability(self) -> None:
        sess = self._build(permission_mode="bypassPermissions")
        pc = sess.tool_context.permission_context
        self.assertEqual(pc.mode, "bypassPermissions")
        self.assertTrue(pc.is_bypass_permissions_mode_available)

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

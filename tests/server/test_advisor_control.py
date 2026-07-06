"""Agent-server /advisor wiring: the ``advisor`` control bridges to the
command-system implementation (advisor_command_call) with the session's
provider, replying ``{ok, text}``. Covers: bare status query, set
(``<provider>:<model>``), off/unset round-trip, unknown-provider error
text, the exception guard on the control channel, the restart case
(config persisted by a prior session must be visible and clearable even
though the session's seeded app-state store doesn't carry advisor
fields), and the multi-session transport gate."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.server.agent_server import AgentServerConfig, _AgentSession


class _IsolatedConfig:
    """Redirect ALL config persistence to a tmp dir.

    Same idiom as tests/test_advisor_command.py::_IsolatedEnv — the
    module-level path constants in ``src/config.py`` are read via the
    module reference at write time, so they're swapped in place and
    restored on exit. Seeds a ``providers`` map so the /advisor
    ``<provider>:<model>`` validation has real keys to accept.
    """

    def __enter__(self):
        import src.config as cfg_mod
        self._cfg_mod = cfg_mod
        self._tmp = Path(tempfile.mkdtemp(prefix="advisor_ctl_"))
        self._saved = (
            cfg_mod.GLOBAL_CONFIG_FILE,
            cfg_mod.HISTORY_FILE,
            cfg_mod.GLOBAL_CONFIG_DIR,
        )
        cfg_mod.GLOBAL_CONFIG_FILE = self._tmp / ".clawcodex" / "config.json"
        cfg_mod.HISTORY_FILE = self._tmp / ".clawcodex" / "history.jsonl"
        cfg_mod.GLOBAL_CONFIG_DIR = self._tmp / ".clawcodex"
        cfg_mod._default_manager = None

        self._saved_env = os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
        from src.settings.settings import invalidate_settings_cache
        invalidate_settings_cache()

        mgr = cfg_mod._get_default_manager()
        cfg = mgr.load_global()
        cfg["providers"] = {
            "deepseek": {
                "api_key": "k",
                "base_url": "https://api.deepseek.com",
                "default_model": "deepseek-v4-pro",
            },
            "zai": {
                "api_key": "k",
                "base_url": "https://api.z.ai/api/coding/paas/v4",
                "default_model": "glm-5.2",
            },
        }
        mgr.save_global(cfg)
        return self

    def __exit__(self, *a):
        cfg_mod = self._cfg_mod
        (
            cfg_mod.GLOBAL_CONFIG_FILE,
            cfg_mod.HISTORY_FILE,
            cfg_mod.GLOBAL_CONFIG_DIR,
        ) = self._saved
        cfg_mod._default_manager = None
        if self._saved_env is not None:
            os.environ["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] = self._saved_env
        from src.settings.settings import invalidate_settings_cache
        invalidate_settings_cache()


def _make_session(single_session: bool = True) -> tuple[_AgentSession, list[dict]]:
    # single_session=True matches the production stdio transport (the TUI
    # child) — the only transport /advisor is available on.
    emitted: list[dict] = []
    sess = _AgentSession(
        session_id="advisor-sess", cwd="/tmp",
        config=AgentServerConfig(single_session=single_session),
        loop=MagicMock(), out_queue=MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)  # type: ignore[method-assign]
    # A 3P-looking provider (plain mock ≠ AnthropicProvider) with the
    # test's target main-loop model — decide_advisor_mode lands client-side.
    provider = MagicMock()
    provider.model = "deepseek-v4-pro"
    sess.provider = provider
    return sess, emitted


def _control(sess: _AgentSession, subtype: str, **params) -> None:
    asyncio.run(sess._handle_control_request({
        "type": "control_request",
        "request_id": "req-1",
        "request": {"subtype": subtype, **params},
    }))


def _last_reply(emitted: list[dict]) -> dict:
    for env in reversed(emitted):
        if env.get("type") == "control_response":
            return env["response"]["response"]
    raise AssertionError(f"no control_response in {emitted!r}")


class TestAdvisorControl(unittest.TestCase):
    def test_bare_advisor_reports_unset_status(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "advisor", arg="")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertIn("Advisor: not set", reply["text"])

    def test_set_then_off_round_trip(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "advisor", arg="zai:glm-5.2")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertIn("Advisor set to zai:glm-5.2", reply["text"])
            self.assertIn("client-side", reply["text"])

            # The write must be visible to the query layer's read channel.
            from src.settings.settings import get_settings
            s = get_settings()
            self.assertEqual(s.advisor_model, "glm-5.2")
            self.assertEqual(s.advisor_provider, "zai")
            self.assertTrue(s.advisor_enabled)

            _control(sess, "advisor", arg="off")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertIn("Advisor disabled", reply["text"])
            self.assertFalse(get_settings().advisor_enabled)

    def test_status_after_set_reports_active_pair(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "advisor", arg="zai:glm-5.2")
            _control(sess, "advisor", arg="")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertIn("zai:glm-5.2", reply["text"])
            self.assertIn("client-side", reply["text"])

    def test_unknown_provider_is_a_command_level_error(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "advisor", arg="nosuch:glm-5.2")
            reply = _last_reply(emitted)
            # Transport-level ok — the grammar error is the command's text.
            self.assertTrue(reply["ok"])
            self.assertIn("Unknown provider", reply["text"])
            from src.settings.settings import get_settings
            self.assertFalse(get_settings().advisor_enabled)

    def test_exception_replies_error_instead_of_killing_channel(self) -> None:
        sess, emitted = _make_session()
        with patch(
            "src.command_system.builtins.advisor_command_call",
            side_effect=RuntimeError("boom"),
        ):
            _control(sess, "advisor", arg="")
        reply = _last_reply(emitted)
        self.assertFalse(reply["ok"])
        self.assertIn("boom", reply["error"])

    def test_restart_sees_and_clears_persisted_config(self) -> None:
        """Regression (critic issue 1): a session booted AFTER /advisor was
        persisted must report the configured pair and be able to turn it
        off — even though ``seed_app_state_from_settings`` doesn't seed
        advisor fields into the session's app-state store. The bridge must
        therefore not read/write through the (advisor-blind) store."""
        with _IsolatedConfig():
            # A prior session persisted an advisor config.
            import src.config as cfg_mod
            from src.settings.settings import invalidate_settings_cache
            mgr = cfg_mod._get_default_manager()
            cfg = mgr.load_global()
            cfg["settings"] = {
                "advisor_model": "glm-5.2",
                "advisor_provider": "zai",
                "advisor_enabled": True,
            }
            mgr.save_global(cfg)
            invalidate_settings_cache()

            # Boot a fresh session the way _build_runtime wires it: a store
            # seeded from settings (which carries NO advisor fields).
            sess, emitted = _make_session()
            from src.state.app_state import (
                create_app_state_store,
                seed_app_state_from_settings,
            )
            sess.app_state_store = create_app_state_store(
                seed_app_state_from_settings("deepseek")
            )

            # Status must reflect the persisted config, not store defaults.
            _control(sess, "advisor", arg="")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertIn("zai:glm-5.2", reply["text"])
            self.assertNotIn("not set", reply["text"])

            # Off must actually persist the disable.
            _control(sess, "advisor", arg="off")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertIn("Advisor disabled", reply["text"])
            from src.settings.settings import get_settings
            self.assertFalse(get_settings().advisor_enabled)
            self.assertFalse(get_settings().advisor_model)

    def test_multi_session_transport_is_refused(self) -> None:
        """/advisor persists user-level settings; on the multi-session WS
        transport that would flip the advisor for every session on the
        host (critic issue 2). The control must refuse."""
        sess, emitted = _make_session(single_session=False)
        _control(sess, "advisor", arg="zai:glm-5.2")
        reply = _last_reply(emitted)
        self.assertFalse(reply["ok"])
        self.assertIn("single-session", reply["error"])


if __name__ == "__main__":
    unittest.main()

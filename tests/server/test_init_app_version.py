"""The ``system/init`` frame carries the clawcodex APP version.

The Ink client renders the header box title as ``clawcodex v{version}``
(ui-tui/src/components/branding.tsx) and gates session-ready on the field being
non-empty (ui-tui/src/app/useSessionLifecycle.ts). Before this, the client
supplied that string from a hand-maintained constant of its own, which drifted
two releases behind (banner read v1.4.0 on a v1.6.0 build). The backend is the
only party that knows what is actually running, so it reports it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


def _reset_all() -> None:
    from src.bootstrap.state import reset_state_for_tests
    from src.eco.state import reset_eco
    from src.nano.state import reset_nano_mode
    from src.services.startup_gates import reset_session_trust_for_testing

    reset_state_for_tests()
    reset_eco()
    reset_nano_mode()
    reset_session_trust_for_testing()


class TestInitCarriesAppVersion(unittest.TestCase):
    """Real ``_AgentSession`` against the keyless ``ollama`` provider, with
    global config redirected to a temp dir (the tests/nano/test_nano_tui.py
    harness shape)."""

    def setUp(self) -> None:
        _reset_all()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.ws = root / "ws"
        self.ws.mkdir()
        config_dir = root / "config-home"
        config_dir.mkdir()
        global_path = config_dir / "config.json"
        global_path.write_text(json.dumps({}), encoding="utf-8")
        self._patches = [
            patch("src.config.get_global_config_path", return_value=global_path),
            patch("src.config.GLOBAL_CONFIG_DIR", str(config_dir)),
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

    def _init_frame(self) -> dict:
        from src.server.agent_server import (
            AgentServerConfig,
            _AgentSession,
            _build_runtime,
        )

        sess = _AgentSession(
            session_id="s-version",
            cwd=str(self.ws),
            config=AgentServerConfig(provider_name="ollama", single_session=True),
            loop=MagicMock(),
            out_queue=MagicMock(),
        )
        _build_runtime(sess, None)
        self.assertIsNone(sess.init_error, f"runtime build failed: {sess.init_error}")
        sess.emit_init()
        frames = [
            call.args[1]
            for call in sess.loop.call_soon_threadsafe.call_args_list
            if len(call.args) == 2 and isinstance(call.args[1], dict)
        ]
        init = [
            f for f in frames
            if f.get("type") == "system" and f.get("subtype") == "init"
        ]
        self.assertTrue(init, "emit_init produced no system/init frame")
        return init[-1]

    def test_init_reports_the_running_app_version(self) -> None:
        from src import __version__

        init = self._init_frame()
        self.assertEqual(init["version"], __version__)

    def test_version_is_non_empty_so_the_client_reaches_ready(self) -> None:
        # useSessionLifecycle gates `status: 'ready'` on this being truthy —
        # an empty string would strand the session at "starting agent…".
        init = self._init_frame()
        self.assertIsInstance(init["version"], str)
        self.assertTrue(init["version"].strip())

    def test_app_version_is_distinct_from_protocol_version(self) -> None:
        # Two different things that both live on this frame: `version` is the
        # release the user installed, `protocol_version` versions the wire
        # format. Conflating them would put the wrong number in the banner.
        from src.server.agent_server import PROTOCOL_VERSION

        init = self._init_frame()
        self.assertEqual(init["protocol_version"], PROTOCOL_VERSION)
        self.assertIn("version", init)


if __name__ == "__main__":
    unittest.main()

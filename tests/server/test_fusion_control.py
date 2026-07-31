"""Agent-server fusion wiring.

Two surfaces:

* the ``fusion`` control, which bridges to ``fusion_command_call`` and
  replies ``{ok, text}`` (the /advisor contract), and
* ``set_model``, which must recognise a fusion model by name and install a
  ``FusionProvider`` instead of poking ``provider.model`` — plus leave one
  again for a plain model.

Also covers the picker listing (``_available_models``) and the
``single_session`` gate.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock

from src.server.agent_server import AgentServerConfig, _AgentSession


class _IsolatedConfig:
    """Redirect config persistence to a tmp dir (the test_advisor_control idiom).

    Seeds a ``providers`` map so fusion selector validation has real keys,
    and clears the process-wide vision-description cache so call counts
    cannot leak between tests.
    """

    def __enter__(self):
        import src.config as cfg_mod

        self._cfg_mod = cfg_mod
        self._tmp = Path(tempfile.mkdtemp(prefix="fusion_ctl_"))
        self._saved = (
            cfg_mod.GLOBAL_CONFIG_FILE,
            cfg_mod.HISTORY_FILE,
            cfg_mod.GLOBAL_CONFIG_DIR,
        )
        cfg_mod.GLOBAL_CONFIG_FILE = self._tmp / ".clawcodex" / "config.json"
        cfg_mod.HISTORY_FILE = self._tmp / ".clawcodex" / "history.jsonl"
        cfg_mod.GLOBAL_CONFIG_DIR = self._tmp / ".clawcodex"
        cfg_mod._default_manager = None

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
            "openrouter": {
                "api_key": "k",
                "base_url": "https://openrouter.ai/api/v1",
                "default_model": "deepseek/deepseek-v4-pro",
            },
        }
        mgr.save_global(cfg)

        from src.providers.fusion_provider import clear_vision_cache

        clear_vision_cache()
        return self

    def __exit__(self, *a):
        # The active-provider supplier and app-state singletons are
        # module-level; leaving them set would bleed into later tests.
        try:
            from src.state.app_state import (
                reset_state_for_tests,
                set_active_provider_supplier,
            )

            set_active_provider_supplier(None)
            reset_state_for_tests()
        except Exception:  # noqa: BLE001
            pass
        cfg_mod = self._cfg_mod
        (
            cfg_mod.GLOBAL_CONFIG_FILE,
            cfg_mod.HISTORY_FILE,
            cfg_mod.GLOBAL_CONFIG_DIR,
        ) = self._saved
        cfg_mod._default_manager = None
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()
        shutil.rmtree(self._tmp, ignore_errors=True)


BASE = "deepseek:deepseek-v4-pro"
VISION = "openrouter:google/gemini-2.5-flash"


def _make_session(single_session: bool = True) -> tuple[_AgentSession, list[dict]]:
    emitted: list[dict] = []
    sess = _AgentSession(
        session_id="fusion-sess",
        cwd="/tmp",
        config=AgentServerConfig(single_session=single_session),
        loop=MagicMock(),
        out_queue=MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)  # type: ignore[method-assign]
    provider = MagicMock()
    provider.model = "deepseek-v4-pro"
    provider.get_available_models = lambda: ["deepseek-v4-pro", "deepseek-v4-flash"]
    sess.provider = provider
    sess.provider_name = "deepseek"
    # An AppState store, as `_build_runtime` creates for single_session.
    # Without it `_dispatch_app_state` is a NO-OP, so nothing persists and
    # any assertion about the saved model passes or fails for the wrong
    # reason (the restart-blind-store trap).
    from src.state.app_state import (
        AppState,
        create_app_state_store,
        set_active_provider_supplier,
    )

    set_active_provider_supplier(lambda: sess.provider_name)
    sess.app_state_store = create_app_state_store(AppState())
    return sess, emitted


def _control(sess: _AgentSession, subtype: str, **params) -> None:
    asyncio.run(
        sess._handle_control_request(
            {
                "type": "control_request",
                "request_id": "req-1",
                "request": {"subtype": subtype, **params},
            }
        )
    )


def _last_reply(emitted: list[dict]) -> dict:
    for env in reversed(emitted):
        if env.get("type") == "control_response":
            return env["response"]["response"]
    raise AssertionError(f"no control_response in {emitted!r}")


class TestFusionControl(unittest.TestCase):
    def test_bare_fusion_lists_nothing_with_guidance(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg="")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertIn("No fusion models saved", reply["text"])

    def test_create_then_list_round_trip(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertIn("Created fusion model 'dsv'", reply["text"])

            _control(sess, "fusion", arg="list")
            text = _last_reply(emitted)["text"]
            self.assertIn("dsv", text)
            self.assertIn(BASE, text)
            self.assertIn(VISION, text)

    def test_create_without_a_name_derives_one(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create {BASE} {VISION}")
            reply = _last_reply(emitted)
            # CCR's capability-obvious naming: <base>-V.
            self.assertIn("deepseek-v4-pro-V", reply["text"])

    def test_command_level_rejection_rides_text_with_ok_true(self) -> None:
        # The /advisor contract: transport ok=True whenever the command ran;
        # only exceptions and the transport gate produce ok=False.
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg="create x nocolon openrouter:v")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertIn("<provider>:<model>", reply["text"])

    def test_delete_and_disable(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "fusion", arg="disable dsv")
            self.assertIn("disabled", _last_reply(emitted)["text"])
            _control(sess, "fusion", arg="list")
            self.assertIn("(disabled)", _last_reply(emitted)["text"])
            _control(sess, "fusion", arg="delete dsv")
            self.assertIn("Deleted", _last_reply(emitted)["text"])
            _control(sess, "fusion", arg="list")
            self.assertIn("No fusion models saved", _last_reply(emitted)["text"])

    def test_multi_session_transport_is_refused(self) -> None:
        # Fusion models are persisted user-level config; on the shared WS
        # transport one client's delete would hit every session on the host.
        with _IsolatedConfig():
            sess, emitted = _make_session(single_session=False)
            _control(sess, "fusion", arg="list")
            reply = _last_reply(emitted)
            self.assertFalse(reply["ok"])
            self.assertIn("single-session", reply["error"])


class TestFusionModelSelection(unittest.TestCase):
    def test_available_models_lists_fusion_models_first(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            models = sess._available_models()
            # CCR: a saved fusion model "appears in routing … like a normal
            # model" — the picker is this client's equivalent surface.
            self.assertEqual(models[0], "dsv")
            self.assertIn("deepseek-v4-pro", models)

    def test_disabled_fusion_models_are_not_listed(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "fusion", arg="disable dsv")
            self.assertNotIn("dsv", sess._available_models())

    def test_set_model_installs_a_fusion_provider(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"], reply)
            # The fusion NAME is echoed as the switch result (what the user
            # picked); fusion_base carries the id actually on the wire.
            self.assertEqual(reply["model"], "dsv")
            self.assertEqual(reply["fusion"], "dsv")
            self.assertEqual(reply["fusion_base"], BASE)
            self.assertEqual(reply["fusion_vision"], VISION)

            from src.providers.fusion_provider import FusionProvider

            self.assertIsInstance(sess.provider, FusionProvider)
            # .model stays the BASE id so cost/context-window lookups work.
            self.assertEqual(sess.provider.model, "deepseek-v4-pro")
            self.assertEqual(sess.provider.fusion_name, "dsv")

    def test_get_settings_reports_the_active_fusion_model(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")
            _control(sess, "get_settings")
            reply = _last_reply(emitted)
            self.assertEqual(reply["fusion"], "dsv")
            self.assertEqual(reply["model"], "deepseek-v4-pro")

    def test_get_settings_reports_empty_fusion_when_not_fused(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()

            # A plain object, NOT the MagicMock _make_session installs: a
            # mock answers every attribute, so it would report a truthy
            # fusion_name and hide the real behaviour. It also stands in for
            # the wire-safety case — whatever a duck-typed provider answers,
            # this field must serialize as a string.
            class PlainProvider:
                model = "deepseek-v4-pro"

                def get_available_models(self):
                    return ["deepseek-v4-pro"]

            sess.provider = PlainProvider()
            _control(sess, "get_settings")
            self.assertEqual(_last_reply(emitted)["fusion"], "")

    def test_get_settings_fusion_field_is_always_a_string(self) -> None:
        # A provider answering a non-string for fusion_name must not put an
        # unserializable object on the NDJSON control channel.
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "get_settings")
            self.assertIsInstance(_last_reply(emitted)["fusion"], str)

    def test_switching_away_from_fusion_drops_the_wrapper(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")
            from src.providers.fusion_provider import FusionProvider

            self.assertIsInstance(sess.provider, FusionProvider)

            _control(sess, "set_model", model="deepseek-v4-flash")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"], reply)
            # Unwrapped, so isinstance(provider, AnthropicProvider) checks
            # downstream see the real provider and the session stops paying
            # for substitution it no longer needs.
            self.assertNotIsInstance(sess.provider, FusionProvider)
            self.assertEqual(sess.provider.model, "deepseek-v4-flash")

    def test_disabled_fusion_model_cannot_be_selected(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "fusion", arg="disable dsv")
            _control(sess, "set_model", model="dsv")
            reply = _last_reply(emitted)
            self.assertFalse(reply["ok"])
            self.assertIn("disabled", reply["error"])
            self.assertIn("/fusion enable dsv", reply["error"])

    def test_mid_turn_switch_away_from_fusion_is_refused(self) -> None:
        # Un-fusing replaces provider AND tool_registry; the turn runs on the
        # worker thread while this handler runs on the main loop, so a
        # mid-turn swap pulls the registry out from under live tool dispatch.
        # /provider and the fusion branch both refuse; this must too.
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")
            from src.providers.fusion_provider import FusionProvider

            self.assertIsInstance(sess.provider, FusionProvider)

            sess._current_abort = object()  # simulate an active turn
            try:
                _control(sess, "set_model", model="deepseek-v4-flash")
                reply = _last_reply(emitted)
                self.assertFalse(reply["ok"], reply)
                self.assertIn("active turn", reply["error"])
                # Still fused: nothing was swapped.
                self.assertIsInstance(sess.provider, FusionProvider)
            finally:
                sess._current_abort = None

    def test_install_provider_failure_still_replies(self) -> None:
        # _install_provider rebuilds the registry and re-registers MCP tools;
        # an escape would reply NOTHING — hanging the client's controlQuery on
        # stdio and dropping the whole connection on the WS transport.
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")

            def boom(*a, **k):
                raise RuntimeError("registry build blew up")

            sess._install_provider = boom  # type: ignore[method-assign]
            _control(sess, "set_model", model="deepseek-v4-flash")
            reply = _last_reply(emitted)
            self.assertFalse(reply["ok"])
            self.assertIn("registry build blew up", reply["error"])

    def test_init_envelope_carries_the_fusion_name(self) -> None:
        # Carried on init, not just the set_model reply, so a session started
        # with --model <fusion-name> shows it from the first frame.
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")
            emitted.clear()
            sess.emit_init()
            init = next(e for e in emitted if e.get("subtype") == "init")
            self.assertEqual(init["fusion"], "dsv")
            self.assertEqual(init["model"], "deepseek-v4-pro")

    def test_switch_persists_the_fusion_NAME_not_the_base_model(self) -> None:
        # The restore side resolves this string back to a fusion record, so
        # persisting the base id would restore the next session as the plain
        # base model and silently drop vision.
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")

            from src.settings.settings import get_settings, invalidate_settings_cache

            invalidate_settings_cache()
            s = get_settings()
            self.assertEqual(s.model, "dsv")
            self.assertEqual(s.model_provider, "deepseek")

    def test_persisted_fusion_model_is_restored_at_startup(self) -> None:
        # The round trip: /model dsv → restart → still fused.
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")

            from src.settings.settings import (
                get_persisted_model,
                invalidate_settings_cache,
            )

            invalidate_settings_cache()
            # A fusion model names its own provider, so it restores even when
            # the session default is a DIFFERENT provider — unlike a plain
            # model, which the staleness guard would drop.
            self.assertEqual(get_persisted_model("deepseek"), "dsv")
            self.assertEqual(get_persisted_model("anthropic"), "dsv")

    def test_disabled_fusion_model_is_not_restored(self) -> None:
        # A since-disabled name must fall through to the provider default
        # rather than reaching the wire as a bogus model id.
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")
            _control(sess, "fusion", arg="disable dsv")

            from src.settings.settings import (
                get_persisted_model,
                invalidate_settings_cache,
            )

            invalidate_settings_cache()
            self.assertEqual(get_persisted_model("deepseek"), "")

    def test_deleted_fusion_model_is_not_restored(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")
            _control(sess, "fusion", arg="delete dsv")

            from src.settings.settings import (
                get_persisted_model,
                invalidate_settings_cache,
            )

            invalidate_settings_cache()
            # Falls through to the provider default; "dsv" must never reach
            # the wire as a model id.
            self.assertEqual(get_persisted_model("deepseek"), "")

    def test_a_declined_persisted_fusion_name_never_reaches_the_provider(self) -> None:
        # THE second read path. `get_persisted_model` correctly declines a
        # DISABLED fusion model, but `seed_app_state_from_settings` reads
        # `settings.model` RAW and still yields the fusion name — so the old
        # post-construction `provider.model = seeded.main_loop_model`
        # backstop would assign it anyway and put a string that is not a real
        # model id on the wire. Both paths must agree.
        with _IsolatedConfig():
            from src.settings.settings import (
                get_persisted_model,
                invalidate_settings_cache,
            )
            from src.state.app_state import seed_app_state_from_settings

            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")
            _control(sess, "set_model", model="dsv")
            _control(sess, "fusion", arg="disable dsv")
            invalidate_settings_cache()

            # The raw seed still carries the name — that is the trap.
            self.assertEqual(
                seed_app_state_from_settings("deepseek").main_loop_model, "dsv"
            )
            # The resolution in force declines it.
            self.assertEqual(get_persisted_model("deepseek"), "")
            # And no code path may turn that raw seed into a wire model id.
            import inspect

            from src.server import agent_server as mod

            src = inspect.getsource(mod._build_runtime)
            self.assertNotIn(
                "provider.model = seeded_state.main_loop_model", src,
                "the raw-seed assignment is back — a declined fusion name "
                "would reach the wire",
            )

    def test_store_pinning_keeps_app_state_agreeing_with_the_provider(self) -> None:
        # `_build_runtime` pins the seeded `main_loop_model` to the
        # resolution actually in force. Without it the raw seed wins, and in
        # the disabled-fusion case app state reports 'dsv' while the provider
        # is on the plain default — the client would show a fusion model that
        # is not running. Asserted on the pinning EXPRESSION, since exercising
        # `_build_runtime` needs a full startup.
        import inspect

        from src.server import agent_server as mod

        src = inspect.getsource(mod._build_runtime)
        self.assertIn(
            "main_loop_model=(fusion.name if fusion is not None else model)", src,
            "the store seed is no longer pinned to the resolved selection — "
            "app state can disagree with the provider that was built",
        )

    def test_plain_model_switch_is_unaffected(self) -> None:
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "set_model", model="deepseek-v4-flash")
            reply = _last_reply(emitted)
            self.assertTrue(reply["ok"])
            self.assertNotIn("fusion", reply)

    def test_missing_vision_credentials_refuses_the_switch(self) -> None:
        # Without this check the switch succeeds and every image silently
        # degrades to a "vision model failed" note, mid-task.
        with _IsolatedConfig():
            sess, emitted = _make_session()
            _control(sess, "fusion", arg=f"create dsv {BASE} {VISION}")

            import src.config as cfg_mod

            mgr = cfg_mod._get_default_manager()
            cfg = mgr.load_global()
            cfg["providers"]["openrouter"]["api_key"] = ""
            mgr.save_global(cfg)

            # Blanking the CONFIG key is not enough: resolve_api_key falls
            # back to the provider's env vars via get_secret, so on a
            # developer machine that exports OPENROUTER_API_KEY (exactly the
            # machine this feature was built on) the switch would succeed
            # and this test would fail for an environmental reason.
            from src.providers import provider_env_vars

            with unittest.mock.patch.dict(
                os.environ,
                {name: "" for name in provider_env_vars("openrouter")},
                clear=False,
            ):
                for name in provider_env_vars("openrouter"):
                    os.environ.pop(name, None)
                _control(sess, "set_model", model="dsv")

            reply = _last_reply(emitted)
            self.assertFalse(reply["ok"])
            self.assertIn("vision provider", reply["error"])


if __name__ == "__main__":
    unittest.main()

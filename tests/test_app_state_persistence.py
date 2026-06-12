"""#280 — AppState model/verbose/expanded-view persistence round-trips.

The side-effect router must persist /model, /verbose, and the
expanded-view toggle into the user-scoped global config's ``settings``
section, and the read side must restore them: ``get_default_app_state``
seeds verbose/expanded_view, and ``get_persisted_model`` feeds the
entrypoints' model-resolution chain (cli option > settings.model >
provider default_model).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.state.app_state import (
    AppState,
    get_default_app_state,
    on_change_app_state,
    persist_model_choice,
    replace_state,
)


class _IsolatedEnv:
    """Isolate ALL config persistence to a tmp dir (the
    test_advisor_command.py pattern: the module-level path constants are
    evaluated at import time, so they're swapped directly). ``tmp`` is
    exposed so tests can pass it as the ConfigManager cwd, keeping the
    project/local layers hermetic too (the repo itself could grow a
    .claude/config.json one day)."""

    @property
    def tmp(self) -> Path:
        return self._tmp

    def __enter__(self):
        import src.config as cfg_mod

        self._tmp = Path(tempfile.mkdtemp(prefix="appstate_persist_"))
        self._saved_global_path = cfg_mod.GLOBAL_CONFIG_FILE
        self._saved_history_path = cfg_mod.HISTORY_FILE
        cfg_mod.GLOBAL_CONFIG_FILE = self._tmp / ".clawcodex" / "config.json"
        cfg_mod.HISTORY_FILE = self._tmp / ".clawcodex" / "history.jsonl"
        cfg_mod.GLOBAL_CONFIG_DIR = self._tmp / ".clawcodex"
        cfg_mod._default_manager = None
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()
        return self

    def __exit__(self, *a):
        import src.config as cfg_mod

        cfg_mod.GLOBAL_CONFIG_FILE = self._saved_global_path
        cfg_mod.HISTORY_FILE = self._saved_history_path
        cfg_mod.GLOBAL_CONFIG_DIR = self._saved_global_path.parent
        cfg_mod._default_manager = None
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()


def _fire(field: str, value) -> None:
    """Run the side-effect router for a single-field change."""
    old = AppState()
    new = replace_state(old, **{field: value})
    on_change_app_state(old, new)


class TestModelPersistence(unittest.TestCase):
    def test_model_choice_round_trips_for_matching_provider(self) -> None:
        with _IsolatedEnv() as env:
            persist_model_choice(None, "anthropic", "claude-opus-4-7")
            from src.settings.settings import (
                get_persisted_model,
                get_settings,
                invalidate_settings_cache,
            )

            invalidate_settings_cache()  # simulate restart
            self.assertEqual(get_settings().model, "claude-opus-4-7")
            self.assertEqual(get_settings().model_provider, "anthropic")
            self.assertEqual(
                get_persisted_model("anthropic", cwd=env.tmp),
                "claude-opus-4-7",
            )

    def test_model_not_restored_for_other_provider(self) -> None:
        # A model persisted on provider A must not feed provider B an
        # invalid model id at the next launch.
        with _IsolatedEnv() as env:
            persist_model_choice(None, "anthropic", "claude-opus-4-7")
            from src.settings.settings import (
                get_persisted_model,
                invalidate_settings_cache,
            )

            invalidate_settings_cache()
            self.assertIsNone(get_persisted_model("glm", cwd=env.tmp))

    def test_store_path_fires_router_and_persists_pair(self) -> None:
        with _IsolatedEnv() as env:
            from src.state.app_state import create_app_state_store

            store = create_app_state_store()
            persist_model_choice(store, "glm", "zai/glm-5")
            from src.settings.settings import (
                get_persisted_model,
                invalidate_settings_cache,
            )

            invalidate_settings_cache()
            self.assertEqual(
                get_persisted_model("glm", cwd=env.tmp), "zai/glm-5"
            )

    def test_clearing_model_persists_unset(self) -> None:
        with _IsolatedEnv() as env:
            persist_model_choice(None, "anthropic", "claude-opus-4-7")
            persist_model_choice(None, "anthropic", None)
            from src.settings.settings import (
                get_persisted_model,
                invalidate_settings_cache,
            )

            invalidate_settings_cache()
            self.assertIsNone(get_persisted_model("anthropic", cwd=env.tmp))

    def test_get_persisted_model_none_when_unset(self) -> None:
        with _IsolatedEnv() as env:
            from src.settings.settings import get_persisted_model

            self.assertIsNone(get_persisted_model("anthropic", cwd=env.tmp))

    def test_unpaired_legacy_model_is_not_restored(self) -> None:
        # A hand-edited settings.model with no model_provider pairing
        # cannot be trusted in a multi-provider config.
        with _IsolatedEnv() as env:
            import src.config as cfg_mod

            mgr = cfg_mod._get_default_manager()
            cfg = mgr.load_global()
            cfg["settings"] = {"model": "claude-opus-4-7"}
            mgr.save_global(cfg)
            from src.settings.settings import (
                get_persisted_model,
                invalidate_settings_cache,
            )

            invalidate_settings_cache()
            self.assertIsNone(get_persisted_model("anthropic", cwd=env.tmp))


class TestVerbosePersistence(unittest.TestCase):
    def test_verbose_round_trips_through_default_state(self) -> None:
        with _IsolatedEnv():
            _fire("verbose", True)
            from src.settings.settings import invalidate_settings_cache

            invalidate_settings_cache()  # simulate restart
            self.assertTrue(get_default_app_state().verbose)

    def test_verbose_off_round_trips(self) -> None:
        with _IsolatedEnv():
            _fire("verbose", True)
            old = replace_state(AppState(), verbose=True)
            on_change_app_state(old, replace_state(old, verbose=False))
            from src.settings.settings import invalidate_settings_cache

            invalidate_settings_cache()
            self.assertFalse(get_default_app_state().verbose)


class TestExpandedViewPersistence(unittest.TestCase):
    def test_expanded_view_round_trips_through_default_state(self) -> None:
        with _IsolatedEnv():
            _fire("expanded_view", "tasks")
            from src.settings.settings import invalidate_settings_cache

            invalidate_settings_cache()  # simulate restart
            self.assertEqual(get_default_app_state().expanded_view, "tasks")

    def test_unknown_persisted_value_falls_back_to_default(self) -> None:
        with _IsolatedEnv():
            import src.config as cfg_mod

            mgr = cfg_mod._get_default_manager()
            cfg = mgr.load_global()
            cfg["settings"] = {"expanded_view": "bogus-value"}
            mgr.save_global(cfg)
            from src.settings.settings import invalidate_settings_cache

            invalidate_settings_cache()
            self.assertEqual(get_default_app_state().expanded_view, "none")


class TestPersistenceIsFailSoft(unittest.TestCase):
    def test_write_failure_does_not_propagate(self) -> None:
        with _IsolatedEnv():
            import src.config as cfg_mod

            mgr = cfg_mod._get_default_manager()
            with patch.object(
                type(mgr), "save_global", side_effect=OSError("disk full")
            ):
                # Must not raise — the in-memory change still works.
                _fire("verbose", True)

    def test_settings_load_failure_does_not_block_default_state(self) -> None:
        with patch(
            "src.settings.settings.get_settings",
            side_effect=RuntimeError("corrupt settings"),
        ):
            state = get_default_app_state()
            self.assertFalse(state.verbose)
            self.assertEqual(state.expanded_view, "none")


class TestAdvisorHandlersStillPersist(unittest.TestCase):
    """The advisor handlers were deduped onto the chokepoint — pin that
    their persistence semantics are unchanged."""

    def test_advisor_fields_round_trip(self) -> None:
        with _IsolatedEnv():
            _fire("advisor_model", "claude-opus-4-6")
            _fire("advisor_provider", "anthropic")
            _fire("advisor_client_mode", True)
            from src.settings.settings import (
                get_settings,
                invalidate_settings_cache,
            )

            invalidate_settings_cache()
            settings = get_settings()
            self.assertEqual(settings.advisor_model, "claude-opus-4-6")
            self.assertEqual(settings.advisor_provider, "anthropic")
            self.assertTrue(settings.advisor_client_mode)


if __name__ == "__main__":
    unittest.main()

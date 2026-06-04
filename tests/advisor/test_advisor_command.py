"""Tests for the /advisor slash command (src/command_system/builtins.py).

Each branch of the TS reference (typescript/src/commands/advisor.ts) is
covered:
  * no-arg → unset / set / inactive
  * unset / off → clear
  * <model> → resolve + validate + set
  * invalid model / non-advisor model rejection
  * non-supported base model warning (advisor set, but inactive)

The command writes through ``_write_advisor_model`` which prefers the
reactive AppState store when present and falls back to direct settings
writes. Tests cover BOTH paths.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.command_system.builtins import advisor_command_call
from src.command_system.types import CommandContext


def _make_context(
    *,
    store: object | None = None,
    provider: object | None = None,
) -> CommandContext:
    """Build a minimal CommandContext for command tests."""
    root = Path(tempfile.gettempdir())
    return CommandContext(
        workspace_root=root,
        cwd=root,
        conversation=None,
        cost_tracker=None,
        history=None,
        app_state_store=store,
        provider=provider,
    )


def _fake_first_party_provider(model: str = "claude-opus-4-6") -> MagicMock:
    """Create a provider mock that the `/advisor` gate accepts."""
    from src.providers.anthropic_provider import AnthropicProvider
    provider = MagicMock(spec=AnthropicProvider)
    provider.has_custom_endpoint.return_value = False
    provider.model = model
    return provider


class _FakeStore:
    """Minimal stand-in for the reactive AppState store used by tests."""

    def __init__(self, initial=None) -> None:
        from src.state.app_state import AppState
        self.state = initial if initial is not None else AppState()
        self.writes: list = []

    def get_state(self):
        return self.state

    def set_state(self, fn) -> None:
        new_state = fn(self.state)
        self.writes.append(new_state)
        self.state = new_state


class _IsolatedEnv:
    """Context manager: isolate ALL config persistence to a tmp dir.

    ``src/config.py`` evaluates ``GLOBAL_CONFIG_FILE = Path.home() /
    ".clawcodex/config.json"`` at import time, so patching ``HOME``
    after import is too late — writes would land on the real user's
    config file. We monkeypatch the module-level constant directly to
    a fresh tmp path inside each test scope.

    Also resets the settings cache + default manager singleton so
    nothing stale leaks across test boundaries.
    """

    def __init__(self) -> None:
        self._tmp = None
        self._patches: list = []
        self._saved_global_path = None
        self._saved_history_path = None

    def __enter__(self):
        import src.config as cfg_mod
        self._tmp = Path(tempfile.mkdtemp(prefix="advisor_test_"))
        # Save and override the module-level config-path constants.
        # We can't use patch.object for plain Path constants reliably
        # because the writer reads them via the module reference.
        self._saved_global_path = cfg_mod.GLOBAL_CONFIG_FILE
        self._saved_history_path = cfg_mod.HISTORY_FILE
        cfg_mod.GLOBAL_CONFIG_FILE = self._tmp / ".clawcodex" / "config.json"
        cfg_mod.HISTORY_FILE = self._tmp / ".clawcodex" / "history.jsonl"
        cfg_mod.GLOBAL_CONFIG_DIR = self._tmp / ".clawcodex"
        cfg_mod._default_manager = None

        os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
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


class TestAdvisorCommandGate(unittest.TestCase):
    """The /advisor command refuses to run when env-disabled. Provider
    type is no longer a gate — client-side mode covers 3P."""

    def test_refuses_when_env_disabled(self) -> None:
        with _IsolatedEnv():
            with patch.dict(
                os.environ, {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}, clear=False
            ):
                ctx = _make_context(provider=_fake_first_party_provider())
                res = advisor_command_call("anthropic:claude-opus-4-6", ctx)
                self.assertIn("disabled", res.value.lower())

    def test_works_with_non_first_party_provider(self) -> None:
        # Pre-client-side, this rejected. Post-client-side, 3P provider
        # is fine — the advisor runs via the dispatcher's separate API
        # call.
        with _IsolatedEnv():
            provider = MagicMock()  # Not an AnthropicProvider
            provider.model = "gpt-5.4"
            ctx = _make_context(provider=provider)
            res = advisor_command_call("anthropic:claude-opus-4-6", ctx)
            self.assertIn("Advisor set to anthropic:claude-opus-4-6", res.value)


class TestAdvisorCommandStorePath(unittest.TestCase):
    """When a reactive AppState store is wired, writes go through it."""

    def test_no_arg_reports_unset(self) -> None:
        with _IsolatedEnv():
            store = _FakeStore()
            ctx = _make_context(store=store, provider=_fake_first_party_provider())
            res = advisor_command_call("", ctx)
            self.assertIn("not set", res.value)
            self.assertIn("/advisor <provider>:<model>", res.value)
            self.assertEqual(store.writes, [])

    def test_set_advisor_model(self) -> None:
        with _IsolatedEnv():
            store = _FakeStore()
            ctx = _make_context(
                store=store, provider=_fake_first_party_provider("claude-opus-4-6")
            )
            res = advisor_command_call("anthropic:claude-opus-4-6", ctx)
            self.assertIn("Advisor set to anthropic:claude-opus-4-6", res.value)
            # Two writes — model AND provider land separately on the store.
            self.assertEqual(len(store.writes), 2)
            self.assertEqual(store.writes[-1].advisor_model, "claude-opus-4-6")
            self.assertEqual(store.writes[-1].advisor_provider, "anthropic")

    def test_unset_clears_when_set(self) -> None:
        with _IsolatedEnv():
            from src.state.app_state import AppState
            store = _FakeStore(initial=AppState(
                advisor_model="claude-opus-4-6",
                advisor_provider="anthropic",
            ))
            ctx = _make_context(store=store, provider=_fake_first_party_provider())
            res = advisor_command_call("unset", ctx)
            self.assertIn("disabled", res.value.lower())
            self.assertIn("anthropic:claude-opus-4-6", res.value)
            self.assertEqual(store.writes[-1].advisor_model, None)
            self.assertEqual(store.writes[-1].advisor_provider, None)

    def test_unset_idempotent_when_not_set(self) -> None:
        with _IsolatedEnv():
            store = _FakeStore()
            ctx = _make_context(store=store, provider=_fake_first_party_provider())
            res = advisor_command_call("off", ctx)
            self.assertIn("already unset", res.value.lower())
            self.assertEqual(store.writes, [])

    def test_client_side_when_base_model_unsupported_for_server(self) -> None:
        # Pre-client-side: this case warned "not supported". Now the
        # base model just falls back to client-side dispatch — the
        # command reports the actual mode chosen.
        with _IsolatedEnv():
            store = _FakeStore()
            provider = _fake_first_party_provider("claude-opus-4-5")
            ctx = _make_context(store=store, provider=provider)
            res = advisor_command_call("anthropic:claude-opus-4-6", ctx)
            self.assertIn("Advisor set to anthropic:claude-opus-4-6", res.value)
            self.assertIn("client-side", res.value.lower())

    def test_no_arg_shows_client_side_for_unsupported_base(self) -> None:
        with _IsolatedEnv():
            from src.state.app_state import AppState
            store = _FakeStore(initial=AppState(
                advisor_model="claude-opus-4-6",
                advisor_provider="anthropic",
            ))
            provider = _fake_first_party_provider("claude-opus-4-5")
            ctx = _make_context(store=store, provider=provider)
            res = advisor_command_call("", ctx)
            self.assertIn("Advisor: anthropic:claude-opus-4-6", res.value)
            # Now active client-side rather than "inactive".
            self.assertIn("client-side", res.value.lower())

    def test_accepts_non_server_side_advisor_model(self) -> None:
        # haiku-4-5 isn't valid for server-side, but it works
        # client-side. The command should accept it.
        with _IsolatedEnv():
            store = _FakeStore()
            ctx = _make_context(store=store, provider=_fake_first_party_provider())
            res = advisor_command_call("anthropic:haiku", ctx)
            self.assertIn("Advisor set to", res.value)
            # Two writes — model and provider land separately.
            self.assertEqual(len(store.writes), 2)

    def test_rejects_unknown_provider(self) -> None:
        # An unknown provider key must be rejected — clawcodex needs a
        # registered Provider class to instantiate. The old test
        # (rejects_unroutable_model) checked rejection by MODEL name;
        # that's no longer a thing since the provider is explicit.
        with _IsolatedEnv():
            store = _FakeStore()
            ctx = _make_context(store=store, provider=_fake_first_party_provider())
            res = advisor_command_call("not-a-real-provider-zzz:foo-model", ctx)
            self.assertIn("Unknown provider", res.value)
            self.assertEqual(store.writes, [])

    def test_rejects_missing_colon(self) -> None:
        # Bare model name (no provider:) must be rejected; the old
        # behavior of "/advisor opus" silently inferring is gone.
        with _IsolatedEnv():
            store = _FakeStore()
            ctx = _make_context(store=store, provider=_fake_first_party_provider())
            res = advisor_command_call("claude-opus-4-6", ctx)
            self.assertIn("<provider>:<model>", res.value)
            self.assertEqual(store.writes, [])


class TestAdvisorCommandSettingsPath(unittest.TestCase):
    """When no store is wired, writes go to the global settings file."""

    def test_set_persists_to_settings(self) -> None:
        with _IsolatedEnv():
            ctx = _make_context(provider=_fake_first_party_provider())
            res = advisor_command_call("anthropic:claude-opus-4-6", ctx)
            self.assertIn("Advisor set to anthropic:claude-opus-4-6", res.value)
            # Read back via the settings stack (fresh load).
            from src.settings.settings import get_settings, invalidate_settings_cache
            invalidate_settings_cache()
            self.assertEqual(get_settings().advisor_model, "claude-opus-4-6")

    def test_unset_persists_to_settings(self) -> None:
        with _IsolatedEnv():
            ctx = _make_context(provider=_fake_first_party_provider())
            advisor_command_call("anthropic:claude-opus-4-6", ctx)
            # Now unset it.
            res = advisor_command_call("off", ctx)
            self.assertIn("disabled", res.value.lower())
            from src.settings.settings import get_settings, invalidate_settings_cache
            invalidate_settings_cache()
            self.assertEqual(get_settings().advisor_model, "")

    def test_set_invalidates_settings_cache(self) -> None:
        # After the command runs, the next get_settings() call must
        # observe the new value — otherwise mid-session toggles would
        # be invisible to _call_model_sync until process restart.
        with _IsolatedEnv():
            from src.settings.settings import get_settings
            # Prime the cache.
            self.assertEqual(get_settings().advisor_model, "")
            ctx = _make_context(provider=_fake_first_party_provider())
            advisor_command_call("anthropic:claude-opus-4-6", ctx)
            # No explicit invalidate — the command must do it.
            self.assertEqual(get_settings().advisor_model, "claude-opus-4-6")


class TestAdvisorCommandStorePathInvalidatesCache(unittest.TestCase):
    """When the reactive AppState store is wired, the _on_change handler
    is responsible for persisting to settings AND invalidating the
    cache. Verify the round trip — without this, mid-session toggles
    via the store wouldn't show up in the next _call_model_sync read.
    """

    def test_store_setstate_invalidates_settings_cache(self) -> None:
        with _IsolatedEnv():
            from src.settings.settings import get_settings
            from src.state.app_state import create_app_state_store, replace_state
            store = create_app_state_store()
            # Prime cache.
            self.assertEqual(get_settings().advisor_model, "")
            ctx = _make_context(store=store, provider=_fake_first_party_provider())
            advisor_command_call("anthropic:claude-opus-4-6", ctx)
            # Store handler should have written + invalidated.
            self.assertEqual(get_settings().advisor_model, "claude-opus-4-6")
            # And the store itself reflects the value.
            self.assertEqual(store.get_state().advisor_model, "claude-opus-4-6")


if __name__ == "__main__":
    unittest.main()

"""Project-wide pytest fixtures.

Currently only provides keyring isolation for MCP token-storage tests so
they don't leak entries into the developer's real OS keychain.
"""

from __future__ import annotations

import pytest


class _InMemoryKeyringBackend:
    """Minimal ``keyring.backend.KeyringBackend`` that holds tokens in a
    process-local dict. Used by tests to isolate token storage from the
    real macOS Keychain / Linux secret-service / Windows DPAPI."""

    priority = 1.0  # higher than FailKeyring

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self._store[(service, username)]
        except KeyError:
            from keyring.errors import PasswordDeleteError  # type: ignore

            raise PasswordDeleteError(f"No such entry: {service}/{username}")


@pytest.fixture(autouse=True)
def _isolate_user_permission_settings(tmp_path, monkeypatch):
    """Point the USER permission-settings file at an empty per-test path.

    C1 wired ``setup_permissions`` into the REPL/TUI/headless startup
    paths, which would otherwise read the developer's real
    ``~/.clawcodex/settings.json`` during tests — machine-dependent allow
    rules could then flip dispatch assertions (review-A finding 6). The
    project/local tiers resolve from each test's cwd/workspace and the
    repo intentionally has no ``.clawcodex/`` dir, so only the user tier
    needs pinning.
    """

    from src.permissions import settings_paths

    isolated = str(tmp_path / "isolated-user-settings.json")
    monkeypatch.setattr(settings_paths, "user_settings_path", lambda: isolated)
    # C6: the startup health check would otherwise read the developer's
    # real ~/.clawcodex/config.json in every full-app test — a malformed
    # file on a dev machine would inject warning rows into unrelated
    # assertions.
    import src.config as config_mod

    # The isolated dir mirrors the prod layout (~/.clawcodex) so tests
    # asserting on the path shape (test_config_path_is_in_home) hold.
    _isolated_global_dir = tmp_path / "isolated-home" / ".clawcodex"
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_DIR", _isolated_global_dir)
    # ch02 round-3: GLOBAL_CONFIG_FILE is read independently of the DIR
    # constant (get_global_config_path) — isolate it too so the tier
    # loaders (_load_global_config_env, get_secret's global fallback)
    # never read the developer's real ~/.clawcodex/config.json. Before
    # this, only the DIR was patched and FILE silently kept pointing at
    # the developer's real config (critic nit 4 on PR #322).
    monkeypatch.setattr(
        config_mod,
        "GLOBAL_CONFIG_FILE",
        _isolated_global_dir / "config.json",
    )
    # C8: skip the startup security gates (trust / external includes /
    # bypass acceptance) in full-app tests — every app test would
    # otherwise boot into the trust or bypass dialog and block on input.
    # The chain jumps straight to its tail (C6 warnings + C7 MCP
    # approvals), which is exactly the pre-C8 boot behavior.
    # test_startup_gates_c8.py exercises the real chain via methods
    # captured at import time, before this patch applies.
    try:
        from src.tui.app import ClawCodexTUI

        monkeypatch.setattr(
            ClawCodexTUI,
            "_run_startup_chain",
            lambda self: self._finish_startup_gates(),
        )
    except Exception:
        pass  # textual not installed in this environment
    # ch03 round-3: the active-provider supplier is a module-level slot;
    # tests need it deterministically EMPTY (the "persists '' when
    # unregistered" case) and must not leak a registration across tests.
    # The shared default ConfigManager and the settings cache must reset
    # too: store creation now SEEDS from them (seed_app_state_from_settings),
    # so a test that populates either cache would otherwise bleed into
    # every later store-creating test's seeds (critic major-1 on the ch03
    # round-3 review).
    def _reset_state_seams() -> None:
        try:
            from src.state.app_state import set_active_provider_supplier

            set_active_provider_supplier(None)
        except Exception:
            pass
        try:
            config_mod._default_manager = None
        except Exception:
            pass
        try:
            from src.settings.settings import invalidate_settings_cache

            invalidate_settings_cache()
        except Exception:
            pass

    _reset_state_seams()
    yield
    _reset_state_seams()


@pytest.fixture(autouse=True)
def _isolate_mcp_keyring(request, monkeypatch):
    """Swap ``keyring.get_keyring()`` to a per-test in-memory backend so
    MCP token-storage tests don't leak into the real OS keychain.

    Autouse — applies to every test. Cheap (no external state, no I/O).
    Tests that explicitly want the real keyring can opt out via
    ``@pytest.mark.real_keyring`` (currently unused).
    """
    if "real_keyring" in request.keywords:
        yield
        return
    try:
        import keyring
    except ImportError:
        yield
        return
    fake = _InMemoryKeyringBackend()
    monkeypatch.setattr(keyring, "get_keyring", lambda: fake)
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.setattr(keyring, "delete_password", fake.delete_password)
    yield

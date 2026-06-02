"""Project-wide pytest fixtures.

Currently only provides keyring isolation for MCP token-storage tests so
they don't leak entries into the developer's real OS keychain.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ensure_src_submodules_loaded():
    """Pre-import ``src.config`` and ``src.permissions`` so that string
    paths like ``monkeypatch.setattr('src.config.X', ...)`` resolve.

    Background: pytest's ``monkeypatch.setattr`` walks a dotted path
    via successive ``getattr`` calls. ``getattr(src, 'config')`` only
    succeeds if ``src.config`` is bound as an attribute of the ``src``
    package. ``src/__init__.py`` does ``from .config import ...`` which
    normally binds it, but earlier tests in the same pytest session can
    wipe the binding in two distinct ways:

    1. Aggressive ``monkeypatch.setattr`` / ``del sys.modules['src.X']``
       chains that remove the submodule.
    2. ``sys.modules.pop('src', None)`` — seen in
       ``tests/test_downstream_cli_dispatch.py::test_run_cli_version_short_circuit``.
       After this, a plain ``import src.permissions`` does NOT bind
       ``permissions`` onto the freshly-imported ``src`` module because
       Python's import machinery sees the submodule already cached in
       ``sys.modules`` and skips the parent-binding step. The attribute
       then does not exist on the new ``src`` module, and any subsequent
       ``monkeypatch.setattr('src.permissions.X', ...)`` fails with
       ``AttributeError: module 'src' has no attribute 'permissions'``.

    The robust fix is to import normally, then explicitly
    ``setattr(src, 'config', ...)`` and ``setattr(src, 'permissions', ...)``
    to force-bind the submodules on whichever ``src`` object is currently
    in ``sys.modules``. This is cheap (one dict lookup + one attribute
    write per test) and is robust against any test-pollution pattern.

    This was a pre-existing cross-file isolation bug surfaced when
    ``tests/test_downstream_cli_dispatch.py`` started using
    ``monkeypatch.setattr('src.config.X', ...)`` / ``'src.permissions.X'``
    in batch runs.
    """
    import src as _src_pkg
    import src.config as _config_mod
    import src.permissions as _perms_mod
    # Force-bind on the current ``src`` module object (whichever one
    # is in ``sys.modules`` right now) so pytest's monkeypatch can
    # resolve dotted paths regardless of any prior ``sys.modules.pop``
    # shenanigans.
    _src_pkg.config = _config_mod
    _src_pkg.permissions = _perms_mod
    yield


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

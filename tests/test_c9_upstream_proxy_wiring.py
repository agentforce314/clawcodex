"""C9 — CCR upstream-proxy wiring (env-gated, no-op by default).

The register-fn indirection (subprocess_env ← get_upstream_proxy_env) + the
guarded entrypoint init. The MOST important assertion is the default no-op:
with CLAUDE_CODE_REMOTE unset, nothing changes.
"""
from __future__ import annotations

import asyncio

import pytest

from src.utils.subprocess_env import (
    register_upstream_proxy_env_fn,
    subprocess_env,
)


@pytest.fixture(autouse=True)
def _clear_registration():
    register_upstream_proxy_env_fn(None)
    yield
    register_upstream_proxy_env_fn(None)


class TestDefaultNoOp:
    def test_no_provider_registered_env_unchanged(self):
        # THE default path: no provider → subprocess_env is byte-for-byte the base
        base = {"PATH": "/usr/bin", "FOO": "bar"}
        assert subprocess_env(base) == base

    def test_provider_returning_empty_is_noop(self):
        register_upstream_proxy_env_fn(lambda: {})
        base = {"PATH": "/usr/bin"}
        assert subprocess_env(base) == base


class TestProxyMerge:
    def test_registered_provider_merges_proxy_vars(self):
        register_upstream_proxy_env_fn(
            lambda: {"HTTPS_PROXY": "http://127.0.0.1:9", "SSL_CERT_FILE": "/ca.pem"})
        out = subprocess_env({"PATH": "/usr/bin"})
        assert out["HTTPS_PROXY"] == "http://127.0.0.1:9"
        assert out["SSL_CERT_FILE"] == "/ca.pem"
        assert out["PATH"] == "/usr/bin"

    def test_proxy_vars_survive_the_scrub(self):
        # proxy env is merged AFTER the scrub, so an injected recipe isn't stripped
        register_upstream_proxy_env_fn(lambda: {"HTTPS_PROXY": "http://127.0.0.1:9"})
        base = {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
                "ANTHROPIC_API_KEY": "secret", "HTTPS_PROXY": ""}
        out = subprocess_env(base)
        assert "ANTHROPIC_API_KEY" not in out          # scrubbed
        assert out["HTTPS_PROXY"] == "http://127.0.0.1:9"  # merged, survives

    def test_provider_failure_is_fail_open(self):
        def _boom():
            raise RuntimeError("proxy down")
        register_upstream_proxy_env_fn(_boom)
        # must not raise — spawning can't be broken by a proxy-env failure
        out = subprocess_env({"PATH": "/usr/bin"})
        assert out["PATH"] == "/usr/bin"


class TestEntrypointGuard:
    def test_maybe_init_noop_without_env(self, monkeypatch):
        from src.entrypoints.agent_server_cli import _maybe_init_upstream_proxy
        from src.utils import subprocess_env as se
        monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
        asyncio.run(_maybe_init_upstream_proxy())
        # no provider was registered (the gate short-circuited before import)
        assert se._upstream_proxy_env_fn is None

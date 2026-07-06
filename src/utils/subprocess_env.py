"""Subprocess environment scrubbing — port of utils/subprocessEnv.ts.

The canonical "env for a spawned child process" chokepoint. When
``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`` is truthy, it strips a fixed set of
secret env vars (+ their ``INPUT_`` GitHub-Action twins) from the child's
environment — an anti-exfiltration control so a prompt-injected Bash command
cannot read a credential via shell expansion (``${ANTHROPIC_API_KEY}``) in a
subprocess. Gated because ``claude-code-action`` sets the flag; a plain local
CLI leaves the env untouched (parity with TS).

TS's ``subprocessEnv`` also merges the upstream-proxy env via
``registerUpstreamProxyEnvFn`` (subprocessEnv.ts) — an indirection so this
module doesn't statically import ``upstreamproxy`` (which pulls asyncio/ssl/the
relay). C9 wires that: the CCR-remote entrypoint registers
``get_upstream_proxy_env`` when ``CLAUDE_CODE_REMOTE`` is set, and
``subprocess_env`` merges its result AFTER the scrub (so the injected
``HTTPS_PROXY``/``*_CA_BUNDLE`` recipe survives the anti-exfiltration strip).
Default (no registration) → the merge is a no-op and behaviour is unchanged.
"""

from __future__ import annotations

import os
from typing import Callable

#: Registered by the CCR-remote entrypoint (register_upstream_proxy_env_fn).
#: ``None`` in every non-remote build — the merge below is then a no-op, so the
#: default subprocess environment is byte-for-byte unchanged.
_upstream_proxy_env_fn: Callable[[], dict[str, str]] | None = None


def register_upstream_proxy_env_fn(fn: Callable[[], dict[str, str]] | None) -> None:
    """Register (or clear with ``None``) the upstream-proxy env provider.

    Port of ``registerUpstreamProxyEnvFn`` (subprocessEnv.ts): the CCR-remote
    entrypoint passes ``get_upstream_proxy_env`` so ``subprocess_env`` can inject
    the proxy recipe into every spawned child WITHOUT this module statically
    importing the upstreamproxy package. Idempotent; test-only callers clear it
    with ``None``."""
    global _upstream_proxy_env_fn
    _upstream_proxy_env_fn = fn

# Verbatim from subprocessEnv.ts GHA_SUBPROCESS_SCRUB (23 vars).
_GHA_SUBPROCESS_SCRUB: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_CUSTOM_HEADERS",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_RUNTIME_URL",
    "ALL_INPUTS",
    "OVERRIDE_GITHUB_TOKEN",
    "DEFAULT_WORKFLOW_TOKEN",
    "SSH_SIGNING_KEY",
)

_TRUTHY = {"1", "true", "yes", "on"}


def _is_env_truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in _TRUTHY


def subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return the environment a child process should inherit.

    ``base`` defaults to a copy of ``os.environ``. When
    ``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`` is truthy, the scrub set (+ ``INPUT_``
    twins) is removed; otherwise ``base`` is returned unchanged (parity with
    TS's non-gated pass-through). Always returns a fresh dict."""
    env = dict(os.environ if base is None else base)
    if _is_env_truthy(env.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB")):
        for key in _GHA_SUBPROCESS_SCRUB:
            env.pop(key, None)
            env.pop(f"INPUT_{key}", None)  # the GitHub-Action input twin
    # Merge the upstream-proxy recipe AFTER the scrub (TS subprocessEnv order),
    # so an injected HTTPS_PROXY / *_CA_BUNDLE isn't stripped. No-op unless the
    # CCR-remote entrypoint registered a provider. Best-effort: a proxy-env
    # failure must never break child-process spawning.
    if _upstream_proxy_env_fn is not None:
        try:
            proxy_env = _upstream_proxy_env_fn()
            if proxy_env:
                env.update(proxy_env)
        except Exception:  # noqa: BLE001 — proxy env must not break spawning
            import logging

            logging.getLogger(__name__).debug(
                "[upstreamproxy] env provider failed", exc_info=True
            )
    return env


__all__ = ["subprocess_env", "register_upstream_proxy_env_fn"]

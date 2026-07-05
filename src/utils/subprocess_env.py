"""Subprocess environment scrubbing — port of utils/subprocessEnv.ts.

The canonical "env for a spawned child process" chokepoint. When
``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`` is truthy, it strips a fixed set of
secret env vars (+ their ``INPUT_`` GitHub-Action twins) from the child's
environment — an anti-exfiltration control so a prompt-injected Bash command
cannot read a credential via shell expansion (``${ANTHROPIC_API_KEY}``) in a
subprocess. Gated because ``claude-code-action`` sets the flag; a plain local
CLI leaves the env untouched (parity with TS).

NOTE: TS's ``subprocessEnv`` also merges the upstream-proxy env
(``registerUpstreamProxyEnvFn``). That half is intentionally NOT wired here —
the port's upstreamproxy is unwired (the deferred CCR-remote-proxy chapter);
when it lands, merge ``get_upstream_proxy_env()`` into the base below.
"""

from __future__ import annotations

import os

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
    if not _is_env_truthy(env.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB")):
        return env
    for key in _GHA_SUBPROCESS_SCRUB:
        env.pop(key, None)
        env.pop(f"INPUT_{key}", None)  # the GitHub-Action input twin
    return env


__all__ = ["subprocess_env"]

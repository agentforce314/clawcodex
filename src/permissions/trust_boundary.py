"""Two-stage env-var trust boundary — chapter 2 §"The Trust Boundary".

Mirrors TS ``applySafeConfigEnvironmentVariables`` /
``applyConfigEnvironmentVariables`` from
``typescript/src/utils/managedEnv.ts``. The architectural insight: env
vars from project-level config can be poisoned by a malicious clone, so
we read them in two passes — only a *safe subset* applies pre-trust.

The safe subset is a *verbatim port* of TS's ``SAFE_ENV_VARS`` set from
``typescript/src/utils/managedEnvConstants.ts:109-194``. Default-deny:
anything not in ``SAFE_ENV_KEYS`` is treated as unsafe.

The ``UNSAFE_ENV_KEYS`` set is technically redundant with default-deny
but exists for auditability — security reviewers should be able to grep
for ``PATH`` / ``LD_PRELOAD`` / ``NODE_EXTRA_CA_CERTS`` and see them
explicitly excluded.

Plan reference: ``my-docs/ch02-bootstrap-refactoring-plan.md`` P1.1.
"""

from __future__ import annotations

import os
from typing import Mapping

__all__ = [
    "SAFE_ENV_KEYS",
    "UNSAFE_ENV_KEYS",
    "UNSAFE_ENV_PREFIXES",
    "is_safe_env_key",
    "apply_safe_config_environment_variables",
    "apply_full_config_environment_variables",
    "extract_mdm_safe_env",
]


# Allow-list ported verbatim from
# ``typescript/src/utils/managedEnvConstants.ts:109-194``. The exclusions
# (NODE_EXTRA_CA_CERTS, ANTHROPIC_BASE_URL, HTTP_PROXY, etc.) are
# intentional security controls — DO NOT add them here. If a key needs
# to be set pre-trust, port the corresponding TS entry; if it isn't on
# the TS list, it goes through the post-trust
# ``apply_full_config_environment_variables`` instead.
SAFE_ENV_KEYS: frozenset[str] = frozenset({
    # Model selection (Anthropic + Vertex + Bedrock)
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_REGION",
    # Bash tool tuning
    "BASH_DEFAULT_TIMEOUT_MS",
    "BASH_MAX_OUTPUT_LENGTH",
    "BASH_MAX_TIMEOUT_MS",
    "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR",
    # CLAUDE_CODE_* runtime flags
    "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_DISABLE_TERMINAL_TITLE",
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
    "CLAUDE_CODE_IDE_SKIP_AUTO_INSTALL",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
    "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
    "CLAUDE_CODE_SKIP_VERTEX_AUTH",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_GITHUB",
    "CLAUDE_CODE_USE_VERTEX",
    # Feature disables (no security impact)
    "DISABLE_AUTOUPDATER",
    "DISABLE_BUG_COMMAND",
    "DISABLE_COST_WARNINGS",
    "DISABLE_ERROR_REPORTING",
    "DISABLE_FEEDBACK_COMMAND",
    "DISABLE_TELEMETRY",
    # Tool budgets
    "ENABLE_TOOL_SEARCH",
    "MAX_MCP_OUTPUT_TOKENS",
    "MAX_THINKING_TOKENS",
    "MCP_TIMEOUT",
    "MCP_TOOL_TIMEOUT",
    # OpenTelemetry config (sans endpoint, which is dangerous)
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE",
    "OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_LOG_TOOL_DETAILS",
    "OTEL_LOG_USER_PROMPTS",
    "OTEL_LOGS_EXPORT_INTERVAL",
    "OTEL_LOGS_EXPORTER",
    "OTEL_METRIC_EXPORT_INTERVAL",
    "OTEL_METRICS_EXPORTER",
    "OTEL_METRICS_INCLUDE_ACCOUNT_UUID",
    "OTEL_METRICS_INCLUDE_SESSION_ID",
    "OTEL_METRICS_INCLUDE_VERSION",
    "OTEL_RESOURCE_ATTRIBUTES",
    # Misc
    "USE_BUILTIN_RIPGREP",
    "VERTEX_REGION_CLAUDE_3_5_HAIKU",
    "VERTEX_REGION_CLAUDE_3_5_SONNET",
    "VERTEX_REGION_CLAUDE_3_7_SONNET",
    "VERTEX_REGION_CLAUDE_4_0_OPUS",
    "VERTEX_REGION_CLAUDE_4_0_SONNET",
    "VERTEX_REGION_CLAUDE_4_1_OPUS",
    "VERTEX_REGION_CLAUDE_4_5_SONNET",
    "VERTEX_REGION_CLAUDE_4_6_SONNET",
    "VERTEX_REGION_CLAUDE_HAIKU_4_5",
})


# Explicit unsafe set. Redundant with default-deny (anything not in
# SAFE_ENV_KEYS is unsafe) but exists for auditability — security
# reviewers should be able to grep for these names and see them
# explicitly excluded. Anchored to chapter §"The Trust Boundary" and
# TS managedEnvConstants.ts:92-103 ("TRUST ATTACKER-CONTROLLED SERVER"
# / "REDIRECT TO ATTACKER-CONTROLLED SERVER" comment blocks).
UNSAFE_ENV_PREFIXES: tuple[str, ...] = ("LD_", "DYLD_")
UNSAFE_ENV_KEYS: frozenset[str] = frozenset({
    # PATH hijacking / shared-library injection
    "PATH",
    "PYTHONPATH",
    "NODE_OPTIONS",
    # TRUST ATTACKER-CONTROLLED SERVER (TS managedEnvConstants.ts:99-103)
    "NODE_EXTRA_CA_CERTS",
    "NODE_TLS_REJECT_UNAUTHORIZED",
    # REDIRECT TO ATTACKER-CONTROLLED SERVER (TS managedEnvConstants.ts:95-97)
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    # SWITCH TO ATTACKER-CONTROLLED PROJECT (TS managedEnvConstants.ts:105-108)
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
})


def is_safe_env_key(key: str) -> bool:
    """True iff this key is safe to apply pre-trust.

    Match priority: explicit unsafe set > unsafe prefix match > safe
    allow-list. Anything that doesn't match the safe list returns
    False (default-deny).
    """
    if key in UNSAFE_ENV_KEYS:
        return False
    if any(key.startswith(prefix) for prefix in UNSAFE_ENV_PREFIXES):
        return False
    return key in SAFE_ENV_KEYS


def apply_safe_config_environment_variables(
    config_env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> None:
    """Apply only the safe subset of config.env to os.environ.

    Called from ``init()`` before the trust dialog. Mirrors TS
    ``applySafeConfigEnvironmentVariables``. Uses ``setdefault`` — does
    NOT overwrite pre-existing process env (matches TS behavior at
    managedEnv.ts:60-65 which checks ``process.env[key] === undefined``
    before writing).

    ``extra_env`` carries managed-config (MDM) values; ch02 round-2 PR-1
    (G1) consumes the MDM prefetch this way. Order matters: MDM values
    apply FIRST (lowest precedence — they are defaults), then config-file
    values via ``setdefault`` so any prior environment OR earlier MDM
    value wins over a later config-file write. This matches TS where the
    union is built before any single application.
    """
    if extra_env:
        for key, value in extra_env.items():
            if is_safe_env_key(key):
                os.environ.setdefault(key, value)
    if config_env is None:
        config_env = _load_config_env()
    for key, value in config_env.items():
        if is_safe_env_key(key):
            os.environ.setdefault(key, value)


def apply_full_config_environment_variables(
    config_env: Mapping[str, str] | None = None,
) -> None:
    """Apply the full config.env to os.environ, including unsafe.

    Called ONLY after the trust dialog has been accepted. Mirrors TS
    ``applyConfigEnvironmentVariables``. Overwrites pre-existing values
    (full apply: unsafe-vars-from-config win over inherited).
    """
    if config_env is None:
        config_env = _load_config_env()
    for key, value in config_env.items():
        os.environ[key] = value  # full apply: overwrites


def extract_mdm_safe_env(payload: str | None) -> dict[str, str]:
    """Parse an MDM plist-as-JSON payload and return the safe env subset.

    ``payload`` is the stdout from ``plutil -convert json -o -
    /Library/Managed Preferences/com.anthropic.claude-code.plist`` (see
    ``src/prefetch.py:start_mdm_raw_read``). Failure modes (None, empty,
    malformed JSON, missing ``env`` key, non-dict ``env``) all silently
    return ``{}`` — init() must never raise due to MDM failures.

    Only keys passing ``is_safe_env_key`` survive; values are coerced
    to strings (matching the env-var contract).
    """
    if not payload:
        return {}
    import json

    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    env = parsed.get("env")
    if not isinstance(env, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in env.items()
        if v is not None and is_safe_env_key(str(k))
    }


def _load_config_env() -> dict[str, str]:
    """Load the ``env`` subkey from the user's global config.

    Reads from ``ConfigManager.load_global()`` only. TS reads from
    user/policy/flag sources pre-trust (`managedEnv.ts:106-110`);
    project/local sources are attacker-controllable (a malicious clone
    could ship a ``.claude/config.json``) and must be deferred to the
    post-trust ``apply_full_config_environment_variables``.

    Returns ``{}`` when no ``env`` is configured. The lazy import of
    ``ConfigManager`` avoids a circular import at module-load.
    """
    from src.config import ConfigManager  # lazy: avoid early-cycle risk

    cm = ConfigManager()
    global_env = cm.load_global().get("env") or {}
    return {
        str(k): str(v)
        for k, v in global_env.items()
        if v is not None
    }

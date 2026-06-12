"""Two-stage env-var trust boundary — chapter 2 §"The Trust Boundary".

Mirrors TS ``applySafeConfigEnvironmentVariables`` /
``applyConfigEnvironmentVariables`` (``typescript/src/utils/managedEnv.ts``).
The TS model is **source-class based**, not key-class based:

* **Trusted sources** (user-owned): the global config
  (``~/.clawcodex/config.json``) and the user settings file. Their ``env``
  blocks apply IN FULL before the trust dialog (TS managedEnv.ts:125-159 —
  a user's own ``HTTPS_PROXY`` or ``NODE_EXTRA_CA_CERTS`` is legitimate;
  see ``caCertsConfig.ts:59-66`` for the same source distinction).
* **Project-scoped sources** (a clone could ship them): project/local
  config and settings files. Pre-trust, only the ``SAFE_ENV_KEYS`` subset
  of their ``env`` blocks applies (TS managedEnv.ts:173-178); the rest
  waits for :func:`apply_full_config_environment_variables` after the
  user accepts the trust gate.
* **Policy source** (root-owned MDM managed preferences): applies in full,
  last, and — uniquely — may override the inherited shell environment
  (TS applies policySettings last among trusted sources,
  managedEnv.ts:160-172; IT must win).

This module is the **sole writer** of config-sourced environment
variables (ch02 round-3: it absorbed ``secret_store``'s startup applier,
which used to copy the merged — project tiers included — env block into
``os.environ`` with no trust gating).

Precedence — the shell-wins customization
------------------------------------------
TS overwrites ``process.env`` with settings env (``Object.assign``). This
port intentionally diverges for user-owned and project tiers: variables
present in the ORIGINAL process environment (with non-empty values) are
never overridden by config sources, so ``export TAVILY_API_KEY=...``
stays a temporary override (documented contract, see ``secret_store``).
Mechanically this is TS's CCD spawn-env-keys filter
(managedEnv.ts:62-80) promoted to always-on. The MDM/policy tier is the
one exception and overrides everything — that is parity with TS's
*non-CCD* policy precedence (in CCD mode TS strips spawn keys from
policy too, managedEnv.ts:160-163; this port has no CCD host).

The ``UNSAFE_ENV_KEYS`` set is technically redundant with default-deny
but exists for auditability — security reviewers should be able to grep
for ``PATH`` / ``LD_PRELOAD`` / ``NODE_EXTRA_CA_CERTS`` and see them
explicitly excluded **from the project-scoped pre-trust pass**. Trusted
tiers may legitimately set these keys (the classification is
project-source-relative, not absolute).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "SAFE_ENV_KEYS",
    "UNSAFE_ENV_KEYS",
    "UNSAFE_ENV_PREFIXES",
    "is_safe_env_key",
    "apply_safe_config_environment_variables",
    "apply_full_config_environment_variables",
    "establish_session_trust",
    "extract_mdm_env",
    "reset_trust_boundary_for_test_only",
]


# Allow-list ported verbatim from
# ``typescript/src/utils/managedEnvConstants.ts:109-194``. Pre-trust,
# project-scoped sources may only set these. If a key needs to apply
# pre-trust from a project file, port the corresponding TS entry; if it
# isn't on the TS list, it waits for the post-trust full pass.
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
# SAFE_ENV_KEYS is unsafe FOR PROJECT-SCOPED SOURCES) but exists for
# auditability. Anchored to chapter §"The Trust Boundary" and TS
# managedEnvConstants.ts:92-103. NB: trusted tiers (global config, user
# settings, MDM) may legitimately set these keys — see module docstring.
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


# --- module state -----------------------------------------------------------

# Keys present (with non-empty values) in the process environment before the
# first config application. Config tiers never override these; only the MDM
# policy tier may. Mirrors TS ccdSpawnEnvKeys (managedEnv.ts:62-80) promoted
# to always-on. Empty/whitespace-only shell values do NOT reserve a key —
# carries over secret_store's "empty env var counts as unset" semantics.
_shell_env_keys: frozenset[str] | None = None

# MDM env stashed by the safe pass so the full pass can re-assert policy
# precedence without re-reading the plist payload.
_last_mdm_env: dict[str, str] = {}


def reset_trust_boundary_for_test_only() -> None:
    """Reset module state. Test-only (PYTEST_CURRENT_TEST-gated)."""
    if os.environ.get("PYTEST_CURRENT_TEST") is None:
        raise RuntimeError(
            "reset_trust_boundary_for_test_only can only be called in tests"
        )
    global _shell_env_keys, _last_mdm_env
    _shell_env_keys = None
    _last_mdm_env = {}


def _capture_shell_env_keys() -> frozenset[str]:
    global _shell_env_keys
    if _shell_env_keys is None:
        _shell_env_keys = frozenset(
            key for key, value in os.environ.items() if value.strip()
        )
    return _shell_env_keys


def is_safe_env_key(key: str) -> bool:
    """True iff a PROJECT-SCOPED source may set this key pre-trust.

    Match priority: explicit unsafe set > unsafe prefix match > safe
    allow-list (case-insensitive, TS managedEnv.ts:175
    ``key.toUpperCase()``). Default-deny.
    """
    if key in UNSAFE_ENV_KEYS:
        return False
    if any(key.startswith(prefix) for prefix in UNSAFE_ENV_PREFIXES):
        return False
    return key.upper() in SAFE_ENV_KEYS


# --- application core --------------------------------------------------------


def _apply(env: Mapping[str, str], *, override_shell: bool = False) -> None:
    shell_keys = _capture_shell_env_keys()
    for key, value in env.items():
        if not override_shell and key in shell_keys:
            continue
        os.environ[key] = value


def _coerce_env_block(block: Any) -> dict[str, str]:
    """Coerce a raw config/settings ``env`` value into ``{str: str}``.

    Same rules as the retired ``secret_store._coerce_env_map``: str/int/float
    values stringify; bool/None are skipped (booleans are config flags, not
    env text).
    """
    if not isinstance(block, Mapping):
        return {}
    out: dict[str, str] = {}
    for name, value in block.items():
        if not isinstance(name, str) or not name:
            continue
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (str, int, float)):
            out[name] = str(value)
    return out


# --- source loaders (S1..S6 in the round-3 plan) ------------------------------


def _load_global_config_env() -> dict[str, str]:
    """S1: ``env`` from the user's global config. Trusted tier."""
    from src.config import ConfigManager  # lazy: avoid early-cycle risk

    return _coerce_env_block(ConfigManager().load_global().get("env"))


def _read_settings_env(path: str) -> dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return _coerce_env_block(data.get("env"))


def _user_settings_enabled() -> bool:
    # TS gates trusted-source env on isSettingSourceEnabled
    # (managedEnv.ts:145, gh#217 SDK isolation).
    try:
        from src.bootstrap.state import get_allowed_setting_sources

        return "userSettings" in get_allowed_setting_sources()
    except Exception:
        return True


def _load_user_settings_env() -> dict[str, str]:
    """S2: ``env`` from the user settings file. Trusted tier."""
    if not _user_settings_enabled():
        return {}
    from src.permissions import settings_paths

    return _read_settings_env(settings_paths.user_settings_path())


def _load_project_scoped_env(cwd: str | Path | None = None) -> dict[str, str]:
    """S3<S4<S5<S6 merged: project config, project settings, local config,
    local settings (later wins). The committable, untrusted tier family.
    """
    from src import config as config_mod
    from src.permissions import settings_paths

    cwd_str = str(cwd) if cwd is not None else None
    merged: dict[str, str] = {}

    cm = config_mod.ConfigManager(cwd=cwd)
    merged.update(_coerce_env_block(cm.load_project().get("env")))          # S3
    merged.update(
        _read_settings_env(settings_paths.project_settings_path(cwd_str))   # S4
    )
    merged.update(_coerce_env_block(cm.load_local().get("env")))            # S5
    merged.update(
        _read_settings_env(settings_paths.local_settings_path(cwd_str))     # S6
    )
    return merged


# --- the two passes -----------------------------------------------------------


def apply_safe_config_environment_variables(
    config_env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> None:
    """Pre-trust pass. Mirrors TS ``applySafeConfigEnvironmentVariables``
    (managedEnv.ts:125-186).

    Order (later wins, subject to the shell-wins rule — see module
    docstring): trusted tiers IN FULL (global config, then user settings),
    then the SAFE subset of the merged project-scoped tiers, then the MDM
    policy env IN FULL (which alone may override the shell). TS literally
    applies its merged-safe loop after policySettings and relies on merge
    priority for policy survival; applying MDM last is net-equivalent and
    simpler (critic-verified for all key-collision cases).

    ``config_env`` overrides the S1 global-config tier (test seam).
    ``extra_env`` is the MDM payload env from :func:`extract_mdm_env`.
    """
    _apply(
        _load_global_config_env() if config_env is None else dict(config_env)
    )
    _apply(_load_user_settings_env())
    _apply({
        key: value
        for key, value in _load_project_scoped_env().items()
        if is_safe_env_key(key)
    })
    if extra_env:
        global _last_mdm_env
        _last_mdm_env = dict(extra_env)
        _apply(_last_mdm_env, override_shell=True)


def apply_full_config_environment_variables(
    config_env: Mapping[str, str] | None = None,
) -> None:
    """Post-trust pass. Mirrors TS ``applyConfigEnvironmentVariables``
    (managedEnv.ts:192-208): every tier in full, MDM policy last.

    Idempotent and callable repeatedly (TS re-applies at main.tsx:2591 and
    on settings change, onChangeAppState.ts:173). Call sites: implicit
    trust for non-interactive sessions (run_pre_action), previously-trusted
    interactive sessions, and every trust-gate accept path via
    :func:`establish_session_trust`.

    Known divergence (documented): TS follows the full apply with CA/mTLS/
    proxy cache clears + global agent reconfiguration (managedEnv.ts:201-207).
    The port has no global agent layer — already-constructed HTTP clients
    (the opt-in TUI builds one before its gates) keep their construction-time
    proxy/CA settings until next launch; everything reading ``os.environ``
    at call time picks the new values up immediately. Client
    rebuild-on-accept is ch04 (API-layer) follow-up work.
    """
    _apply(
        _load_global_config_env() if config_env is None else dict(config_env)
    )
    _apply(_load_user_settings_env())
    _apply(_load_project_scoped_env())
    if _last_mdm_env:
        _apply(_last_mdm_env, override_shell=True)


def establish_session_trust() -> None:
    """Grant session trust and apply the full environment.

    The single helper every accept path uses (TS pairs
    ``setSessionTrustAccepted(true)`` at interactiveHelpers.tsx:150 with
    ``applyConfigEnvironmentVariables()`` at :194): non-interactive
    implicit trust, previously-accepted interactive sessions, the TUI
    TrustFolderScreen accept, and the legacy-REPL text prompt accept.

    Sets BOTH session-trust flags (bootstrap state + startup_gates) via
    ``grant_session_trust`` — without the sync, a piped-stdout session
    (classified non-interactive, dispatched to the REPL) would be asked
    to trust AFTER its env was already applied.
    """
    from src.services.startup_gates import grant_session_trust  # lazy

    grant_session_trust()
    apply_full_config_environment_variables()


def extract_mdm_env(payload: str | None) -> dict[str, str]:
    """Parse an MDM plist-as-JSON payload and return its env block.

    ``payload`` is the stdout from ``plutil -convert json -o -
    /Library/Managed Preferences/com.anthropic.claude-code.plist`` (see
    ``src/prefetch.py:start_mdm_raw_read``). Failure modes (None, empty,
    malformed JSON, missing/non-dict ``env``) silently return ``{}`` —
    init() must never raise due to MDM failures.

    UNFILTERED (ch02 round-3): the managed-preferences plist is root-owned
    — it is the port's policy tier, which TS applies without a safe-key
    filter (managedEnv.ts:160-172). The previous safe-filtering broke the
    enterprise-proxy use case policy env exists for.
    """
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    env = parsed.get("env")
    if not isinstance(env, dict):
        return {}
    return _coerce_env_block(env)

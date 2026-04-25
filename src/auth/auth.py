"""API key management matching TypeScript auth.ts.

Load order: env var → config file → keychain.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

ApiKeySource = Literal["env", "config", "keychain", "oauth", "unknown"]

# Env vars checked in order
_ANTHROPIC_KEY_ENV_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")
_OPENAI_KEY_ENV_VARS = ("OPENAI_API_KEY",)
_AWS_KEY_ENV_VARS = ("AWS_ACCESS_KEY_ID",)
_GEMINI_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_OPENROUTER_KEY_ENV_VARS = ("OPENROUTER_API_KEY",)
_DEEPSEEK_KEY_ENV_VARS = ("DEEPSEEK_API_KEY",)

# Provider → env var lists
_PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": _ANTHROPIC_KEY_ENV_VARS,
    "openai": _OPENAI_KEY_ENV_VARS,
    "aws": _AWS_KEY_ENV_VARS,
    "gemini": _GEMINI_KEY_ENV_VARS,
    "openrouter": _OPENROUTER_KEY_ENV_VARS,
    "deepseek": _DEEPSEEK_KEY_ENV_VARS,
}

# Simple format validators
_KEY_PATTERNS: dict[str, re.Pattern[str]] = {
    "anthropic": re.compile(r"^sk-ant-[a-zA-Z0-9_-]{20,}$"),
    "openai": re.compile(r"^sk-[a-zA-Z0-9_-]{20,}$"),
    "openrouter": re.compile(r"^sk-or-[a-zA-Z0-9_-]{20,}$"),
    "deepseek": re.compile(r"^sk-[a-zA-Z0-9_-]{20,}$"),
}


@dataclass(frozen=True)
class ApiKeyInfo:
    """Information about a loaded API key."""
    key: str
    provider: str
    source: ApiKeySource
    is_valid_format: bool


def load_api_key(provider: str = "anthropic") -> ApiKeyInfo | None:
    """Load an API key from env, config, or keychain.

    Returns None if no key found.
    """
    # 1. Environment variables
    env_vars = _PROVIDER_ENV_VARS.get(provider, ())
    for var in env_vars:
        value = os.environ.get(var)
        if value:
            return ApiKeyInfo(
                key=value,
                provider=provider,
                source="env",
                is_valid_format=validate_api_key(value, provider),
            )

    # 2. Config file
    try:
        from ..config import load_config
        config = load_config()
        providers = config.get("providers", {})
        provider_cfg = providers.get(provider, {})
        key = provider_cfg.get("api_key", "")
        if key:
            return ApiKeyInfo(
                key=key,
                provider=provider,
                source="config",
                is_valid_format=validate_api_key(key, provider),
            )
    except Exception:
        pass

    # 3. Keychain (macOS only, best-effort)
    key = _load_from_keychain(provider)
    if key:
        return ApiKeyInfo(
            key=key,
            provider=provider,
            source="keychain",
            is_valid_format=validate_api_key(key, provider),
        )

    return None


def validate_api_key(key: str, provider: str = "anthropic") -> bool:
    """Check if key matches expected format for the provider."""
    if not key:
        return False
    pattern = _KEY_PATTERNS.get(provider)
    if pattern:
        return bool(pattern.match(key))
    # Unknown provider — just check non-empty
    return len(key) >= 10


def get_api_key_source(provider: str = "anthropic") -> ApiKeySource:
    """Determine where the API key comes from without loading it."""
    env_vars = _PROVIDER_ENV_VARS.get(provider, ())
    for var in env_vars:
        if os.environ.get(var):
            return "env"
    try:
        from ..config import load_config
        config = load_config()
        key = config.get("providers", {}).get(provider, {}).get("api_key", "")
        if key:
            return "config"
    except Exception:
        pass
    return "unknown"


def _load_from_keychain(provider: str) -> str | None:
    """Try to load API key from macOS keychain."""
    import platform
    if platform.system() != "Darwin":
        return None
    try:
        import subprocess
        service = f"clawcodex-{provider}"
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None

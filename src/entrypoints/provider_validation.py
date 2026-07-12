"""Startup provider-env validation — the port of TS
``validateProviderEnvForStartupOrExit`` (``entrypoints/cli.tsx:149``,
``utils/providerValidation.ts:479-528``).

TS surfaces a broken/incomplete provider configuration at startup instead of
deep inside the first API call. Its validator is built on the integrations
descriptor subsystem (no Python analog), so this is a function-at-altitude
port against this port's own provider registry
(``src/providers/__init__.py``): resolve the effective provider the same way
the launch paths do, then check the same two things the headless path
already checked inline (``headless.py`` pre-ENTRY-2) — provider is known,
and a key-requiring provider has a key. That inline check moved HERE so the
three entry paths (bare interactive, ``clawcodex tui``, headless) share one
implementation and one message.

Exit semantics mirror TS exactly (``shouldExitForStartupProviderValidationError``,
providerValidation.ts:489-508): **non-interactive → print + exit; an
interactive TTY gets a WARNING and continues** — the TUI can surface the
problem and the user can repair it (``clawcodex login`` / the provider
picker) without being kicked out.
"""

from __future__ import annotations

import sys

__all__ = [
    "get_provider_validation_error",
    "validate_provider_at_startup",
]


def get_provider_validation_error(provider_name: str | None) -> str | None:
    """The validation error for the effective provider, or ``None``.

    Side-effect-free. ``provider_name`` is the explicit ``--provider`` value
    (or None → the configured default), matching the resolution the launch
    paths use (``options.provider_name or get_default_provider()``).
    """
    from src.config import get_default_provider, get_provider_config
    from src.providers import provider_has_credentials, resolve_api_key

    name = provider_name or get_default_provider()
    try:
        provider_cfg = get_provider_config(name)
    except Exception as exc:  # noqa: BLE001 — unknown provider / broken config
        return f"error: unable to load provider config: {exc}"

    # Config api_key wins; fall back to the provider's known env vars (e.g.
    # ``DEEPSEEK_API_KEY``). Local providers (Ollama / vLLM / SGLang) need
    # no key; anthropic/openai subscription OAuth logins also pass. Same
    # check the headless path ran inline pre-ENTRY-2; the shared helper
    # keeps this gate consistent with the agent-server's two gates.
    api_key = resolve_api_key(name, provider_cfg)
    if not provider_has_credentials(name, api_key):
        return (
            f"error: API key for provider '{name}' is not configured. "
            "Run `clawcodex login` to set it up."
        )
    return None


def validate_provider_at_startup(
    provider_name: str | None,
    *,
    interactive: bool,
    exit_code: int = 2,
) -> None:
    """Validate; exit for non-interactive surfaces, warn-and-continue for
    interactive ones (the TS split — providerValidation.ts:508-528).

    ``exit_code`` preserves the headless path's historical exit code (2).
    """
    error = get_provider_validation_error(provider_name)
    if error is None:
        return
    if not interactive:
        print(error, file=sys.stderr)
        raise SystemExit(exit_code)
    print(
        "Warning: provider configuration is incomplete.\n"
        f"{error}\n"
        "clawcodex will continue starting so you can repair it "
        "(`clawcodex login` or the provider picker).",
        file=sys.stderr,
    )

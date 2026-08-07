"""/vision — configure the model that answers ``vision_analyze``.

    /vision                        show the current pairing
    /vision <provider>:<model>     set it (and turn the tool on)
    /vision on                     re-enable the last pairing
    /vision off                    disable without forgetting it

Grammar matches ``/advisor``: ``<provider>:<model>``, split on the FIRST
colon so model ids containing ``/`` or further ``:`` survive intact
(``openrouter:z-ai/glm-4.5v``). Both halves are required — clawcodex is
multi-provider and the same model name sits behind several of them, so the
provider cannot be inferred from the model.

Writes the GLOBAL config tier only; :mod:`src.providers.vision_config`
explains why that is a security requirement rather than a convenience. No
``AppState`` field and no ``settings`` key: that config module is the single
reader, so there is nothing to keep in sync.

Every path is headless-safe, so the command behaves identically in the TUI,
``-p`` headless, and the SDK.
"""

from __future__ import annotations

from .types import CommandContext, LocalCommand, LocalCommandResult

_USAGE = "Usage: /vision <provider>:<model> | on | off"


def _configured_providers() -> list[str]:
    """Provider keys present in the global config's ``providers`` map."""
    try:
        from .. import config as cfg_mod

        providers = cfg_mod._get_default_manager().load_global().get("providers")
        if isinstance(providers, dict):
            return sorted(providers.keys())
    except Exception:  # noqa: BLE001
        pass
    return []


def _text(value: str) -> LocalCommandResult:
    return LocalCommandResult(type="text", value=value)


def vision_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    from ..providers.vision_config import (
        load_vision_config,
        save_vision_config,
        set_vision_enabled,
    )

    arg = (args or "").strip()
    current = load_vision_config()

    if not arg:
        if current is None:
            return _text(
                "Vision is not configured. Set one with /vision <provider>:<model> "
                "(e.g. /vision openai:gpt-5.6-luna) — it lets a text-only model "
                "call vision_analyze on an image."
            )
        return _text(f"Vision model: {current.selector}")

    lowered = arg.lower()

    if lowered in ("off", "disable", "none"):
        if current is None:
            return _text("Vision already off.")
        set_vision_enabled(False)
        return _text(f"Vision disabled (was {current.selector}).")

    if lowered in ("on", "enable"):
        # Re-arm a pairing that ``off`` preserved, so the user does not have
        # to retype it. Only meaningful when provider+model are still on disk.
        if current is not None:
            return _text(f"Vision already on ({current.selector}).")
        set_vision_enabled(True)
        restored = load_vision_config()
        if restored is None:
            # ``enabled`` flipped but no pairing was ever saved — say so
            # rather than reporting success for a config that cannot work.
            set_vision_enabled(False)
            return _text(f"No saved vision model to enable. {_USAGE}")
        return _text(f"Vision enabled ({restored.selector}).")

    if ":" not in arg:
        providers = _configured_providers()
        hint = f" Configured providers: {', '.join(providers)}." if providers else ""
        return _text(f"{_USAGE} (got {arg!r}).{hint}")

    provider_part, model_part = arg.split(":", 1)
    provider_part = provider_part.strip()
    model_part = model_part.strip()
    if not provider_part or not model_part:
        return _text(f"Both halves required. {_USAGE} (got {arg!r}).")

    # Validate against the real provider registry, NOT just the config's
    # ``providers`` map. Two reasons the map alone is wrong: it is empty on a
    # fresh install (which would let ANY string through and persist a config
    # that can never work), and it misses aliases that
    # ``get_provider_config`` would happily resolve.
    try:
        from ..config import get_provider_config

        get_provider_config(provider_part)
    except Exception:
        providers = _configured_providers()
        hint = f" Configured providers: {', '.join(providers)}." if providers else ""
        return _text(f"Unknown provider {provider_part!r}.{hint}")

    # Credentials: the tool would otherwise advertise itself, get called, and
    # die on "Missing credentials" — and because Read's stub starts naming it
    # the moment it is configured, the failure would look like a tool bug.
    warning = ""
    try:
        from ..providers import provider_has_credentials, resolve_api_key

        if not provider_has_credentials(provider_part, resolve_api_key(provider_part)):
            warning = (
                f"\nWarning: no API key found for {provider_part}. "
                "Run /login or set its API key env var before using it."
            )
    except Exception:  # noqa: BLE001 — never block configuration on this check
        pass

    # Vision capability is a WARNING, not a refusal: the model table is not
    # exhaustive and a freshly released vision model should not be blocked by
    # our catalogue lagging. Same permissive stance ``supports_vision`` takes.
    try:
        from ..models.capabilities import supports_vision

        if not supports_vision(model_part):
            warning += (
                f"\nNote: {model_part} is recorded as NOT vision-capable. "
                "If that is wrong it will still be used."
            )
    except Exception:  # noqa: BLE001
        pass

    cfg = save_vision_config(provider_part, model_part)
    return _text(
        f"Vision model set to {cfg.selector}. vision_analyze is now available."
        f"{warning}"
    )


VISION_COMMAND = LocalCommand(
    name="vision",
    description="Configure the vision model used by the vision_analyze tool",
    argument_hint="[<provider>:<model> | on | off]",
    supports_non_interactive=True,
)
VISION_COMMAND.set_call(vision_command_call)

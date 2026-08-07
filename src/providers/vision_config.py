"""Configuration for the ``vision_analyze`` tool — a borrowed pair of eyes.

A text-only main model (DeepSeek V4, gpt-oss-120b, …) cannot take an image
block at all: the request 400s with ``unknown variant `image_url`, expected
`text``` before the model gets a turn. A **fusion model**
(``src/providers/fusion_models.py``) fixes that by substituting every image
with a description at the provider layer.

This module configures the other half of the answer: a TOOL the model calls
on demand, with its own question. The two are complementary, not rival —
fusion covers images that are already in flight (a paste, a ``Read`` result)
and cannot be declined; the tool covers "look again, and this time tell me
the VAT line", which fusion's single fixed-prompt pass cannot express.
``fusion_models.py``'s own note says as much: *"there is no per-call tool
invocation"*. Now there is.

Storage
-------
The **global** config tier only (``~/.clawcodex/config.json`` -> ``"vision"``),
read with ``load_global`` / written with ``set_global`` — never the merged
tier.

That is a security decision, and a stronger case than the identical one made
for ``fusionModels``. This key names the provider that RECEIVES IMAGE BYTES.
Settings-block keys merge ``global < project < local``, and a ``vision``
entry there would not be covered by ``_UNTRUSTED_TIER_BLOCKED_KEYS``
(``src/config.py``, which withholds only ``env`` / ``providers`` /
``default_provider`` from committable tiers) — so a checked-out repo could
silently redirect the user's screenshots to a provider of its choosing while
riding the user's stored credentials. A terminal screenshot routinely
contains keys. Keeping the key global-only means a project tier can never
contribute one, so no strip rule is needed.

Deliberately NOT a ``SettingsSchema`` field: ``SettingsSchema.from_dict``
files unknown keys into ``settings.extra`` with no error and no warning, so a
``vision_provider`` key there would read as configured while being inert.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Config key holding the vision-tool configuration (global tier only).
VISION_KEY = "vision"

#: Default per-call ceiling for one vision request. Matches the fusion
#: default (``fusion_models.DEFAULT_VISION_TIMEOUT_MS``) so the two paths
#: behave the same against a slow provider.
DEFAULT_VISION_TIMEOUT_MS = 60_000


@dataclass(frozen=True)
class VisionConfig:
    """A resolved, usable vision configuration.

    Only ever constructed when both halves are present — see
    :func:`load_vision_config`.
    """

    provider: str
    model: str
    #: Extra instruction appended to the per-call prompt. The model's own
    #: ``question`` is the primary steer; this is for standing preferences
    #: ("always transcribe numbers digit by digit").
    prompt: str = ""
    timeout_ms: int = DEFAULT_VISION_TIMEOUT_MS

    @property
    def selector(self) -> str:
        """``provider:model``, the spelling used in user-facing messages."""
        return f"{self.provider}:{self.model}"


def _manager():
    """The shared :class:`~src.config.ConfigManager`.

    Resolved through the module attribute at call time (not imported at
    module scope) so test isolation that re-points the config dir is
    honoured — the ``config.py`` ``_global_config_file`` discipline. Same
    reasoning as ``fusion_models._manager``.
    """
    from src import config as cfg_mod

    return cfg_mod._get_default_manager()


def _coerce_int(value: object, default: int) -> int:
    try:
        out = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return out if out > 0 else default


def load_vision_config() -> VisionConfig | None:
    """The configured vision model, or ``None`` when unusable.

    ``None`` — meaning "the tool stays hidden" — unless ALL of:

    * ``enabled`` is true. Default OFF, mirroring ``advisor_enabled``: this
      spends money at a second vendor, so it is opt-in.
    * ``provider`` AND ``model`` are both non-empty. Half a config is a
      misconfiguration, not a partial feature — clawcodex is multi-provider
      and the same model name lives behind several of them, so the provider
      cannot be inferred from the model. Same both-halves rule the advisor
      enforces for ``advisor_provider`` / ``advisor_model``.

    Never raises: a broken config yields ``None``, because vision is an
    enhancement and a config problem must not brick startup or the tool list.

    Note ``strict`` boolean coercion — ``bool("false")`` is ``True`` in
    Python, so a quoted ``"enabled": "false"`` would otherwise silently turn
    the tool on. hermes hit exactly this (``agent/image_routing.py``) and
    guards it the same way.
    """
    try:
        raw = _manager().load_global().get(VISION_KEY)
    except Exception:  # noqa: BLE001 — an enhancement must not brick startup
        return None
    if not isinstance(raw, dict):
        return None

    enabled = raw.get("enabled")
    if enabled is not True:  # strict: only a real JSON true enables it
        return None

    provider = raw.get("provider")
    model = raw.get("model")
    if not isinstance(provider, str) or not provider.strip():
        return None
    if not isinstance(model, str) or not model.strip():
        return None

    prompt = raw.get("prompt")
    return VisionConfig(
        provider=provider.strip(),
        model=model.strip(),
        prompt=prompt.strip() if isinstance(prompt, str) else "",
        timeout_ms=_coerce_int(raw.get("timeout_ms"), DEFAULT_VISION_TIMEOUT_MS),
    )


def save_vision_config(
    provider: str, model: str, *, prompt: str = "", timeout_ms: int | None = None
) -> VisionConfig:
    """Write the vision block (global tier) and return the resolved config."""
    # Read-modify-write. A fresh block would silently discard ``prompt`` and
    # ``timeout_ms``, which are hand-edited (no command sets them) — so
    # re-pointing the model with /vision would wipe a standing instruction
    # the user had configured, without saying so.
    try:
        existing = _manager().load_global().get(VISION_KEY)
    except Exception:  # noqa: BLE001
        existing = None
    block: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    block.update(
        {"enabled": True, "provider": provider.strip(), "model": model.strip()}
    )
    if prompt.strip():
        block["prompt"] = prompt.strip()
    if timeout_ms is not None:
        block["timeout_ms"] = int(timeout_ms)
    _manager().set_global(VISION_KEY, block)
    return VisionConfig(
        provider=block["provider"],
        model=block["model"],
        prompt=block.get("prompt", ""),
        timeout_ms=_coerce_int(block.get("timeout_ms"), DEFAULT_VISION_TIMEOUT_MS),
    )


def set_vision_enabled(enabled: bool) -> None:
    """Flip the master switch, preserving provider/model so ``/vision on``
    can restore a previous pairing without retyping it."""
    try:
        raw = _manager().load_global().get(VISION_KEY)
    except Exception:  # noqa: BLE001
        raw = None
    block = dict(raw) if isinstance(raw, dict) else {}
    block["enabled"] = bool(enabled)
    _manager().set_global(VISION_KEY, block)


def vision_is_configured() -> bool:
    """Whether ``vision_analyze`` should be offered to the model.

    The tool's ``is_enabled`` gate. Kept separate from
    :func:`load_vision_config` so callers that only need the boolean read as
    such at the call site.
    """
    return load_vision_config() is not None

"""Fusion models — a base reasoning model plus a borrowed vision model.

Port of claude-code-router's **Fusion model** concept
(``reference_projects/claude-code-router``: ``docs/.../configuration/fusion-models.md``,
``fusion-vision.md``, and the lifecycle helpers in
``packages/ui/src/pages/home/shared/virtual-models.ts``).

The problem it solves: several strong reasoning/coding models are text-only.
``deepseek-v4-pro`` hard-rejects any image content block —

    HTTP 400  unknown variant `image_url`, expected `text`

— so pasting a screenshot, ``@screenshot.png``-mentioning one, or letting
``Read`` return an image kills the turn. A Fusion model keeps the base
model's reasoning and *borrows* image understanding from a second,
vision-capable model.

CCR's own framing (``fusion-models.md``): "Fusion keeps the base model's
reasoning, writing, and coding behavior, then adds the missing capability
layer… After saving, a Fusion model appears in routing and Agent Profiles
like a normal model." The naming convention is ``base + capability = new
model``, e.g. ``GLM-5.2 + GLM-5V-Turbo = GLM-5.2V``.

What this module owns
---------------------
The **record and its lifecycle** — create / configure / save / manage —
mirroring CCR's ``createVirtualModelDraft`` → ``validateVirtualModelDraft``
→ ``virtualModelProfileFromDraft`` → persisted ``virtualModelProfiles[]``.
The runtime mechanism lives in :mod:`src.providers.fusion_provider`.

Deliberate divergences from CCR
-------------------------------
* **Transport.** CCR is a *proxy*: it cannot touch the agent loop, so its
  only lever is exposing vision as an MCP tool (``vision_understand``,
  ``packages/core/src/mcp/fusion-vision-mcp.ts``) that the base model may
  choose to call. That cannot fix a *pasted* image — the image block is
  already on the wire and DeepSeek 400s before the model gets a turn.
  clawcodex owns the whole loop, so it does what CCR's docs actually
  describe ("connects a vision model **in front of** the base model…
  passes that visual result to the base model") directly, by substituting
  image blocks in-process. See :mod:`src.providers.fusion_provider`.
* **Scope.** Vision only. CCR also fuses web search, image/video
  generation, and arbitrary MCP tools; those are separate capabilities
  with their own config surfaces and are out of scope here.
* **Selector grammar.** ``provider:model`` (split on the FIRST colon),
  matching clawcodex's existing ``/advisor <provider>:<model>``
  convention, rather than CCR's ``provider/model`` — a bare ``/`` is
  ambiguous with model ids that contain one (``google/gemini-2.5-flash``).
  Splitting on the first colon also keeps OpenRouter's ``:free``-suffixed
  ids intact (``openrouter:deepseek/deepseek-r1:free``).
* **Flat record.** CCR's profile carries match modes (alias/prefix/suffix),
  materialization templates, execution modes, and per-tool schemas. A
  Fusion model here is matched by exact name only, so the record stays a
  flat dataclass instead of a nested profile.

Storage
-------
The **global** config tier only (``~/.clawcodex/config.json`` →
``"fusionModels"``), read with ``load_global`` / written with
``save_global`` — never the merged tier.

That is a security decision, not a convenience one. A fusion model names
the provider that receives image bytes, which makes it exactly the class
of routing config ``_UNTRUSTED_TIER_BLOCKED_KEYS`` (``src/config.py``)
already withholds from committable project/local tiers: a checked-out
repo must not be able to add a fusion model that quietly ships the user's
screenshots to a provider of its choosing while riding the user's stored
credentials. Keeping the key global-only means a project tier can never
contribute one, so no strip rule is needed. Same tier as ``/advisor``'s
secondary-model settings.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

#: Config key holding the saved fusion models (global tier only).
FUSION_MODELS_KEY = "fusionModels"

#: Default per-image vision-call timeout. CCR's ``defaultTimeoutMs`` is
#: 30s (``fusion-vision-mcp.ts``); doubled here because a fused turn makes
#: the vision call inline on the critical path of the user's request, and a
#: timeout costs the whole turn rather than one tool call.
DEFAULT_VISION_TIMEOUT_MS = 60_000

#: Cap on images described per request. A runaway conversation (long
#: history full of screenshots) would otherwise fan out into an unbounded
#: number of serial vision calls on one turn.
DEFAULT_MAX_IMAGES = 8

#: Suffix suggested for a fusion model derived from a base model, following
#: CCR's ``GLM-5.2 + GLM-5V-Turbo = GLM-5.2V`` naming ("use names that make
#: the capability source obvious").
VISION_NAME_SUFFIX = "V"


class FusionModelError(ValueError):
    """A fusion model is invalid, unknown, or conflicts with an existing one.

    A ``ValueError`` subclass so callers that only care about "bad input"
    can catch the broader type, while ``/fusion`` and the agent-server
    controls catch this specifically to render the message verbatim.
    """


@dataclass(frozen=True)
class ModelRef:
    """A ``provider:model`` pair — which endpoint serves which model id."""

    provider: str
    model: str

    @property
    def selector(self) -> str:
        return f"{self.provider}:{self.model}"

    def __str__(self) -> str:  # for f-strings in user-facing messages
        return self.selector


@dataclass(frozen=True)
class FusionModel:
    """A saved fusion model: ``name = base + vision``.

    ``name`` is the id the user selects (``/model <name>``) and the only
    match key — CCR's alias/prefix/suffix match modes are not ported.
    """

    name: str
    base: ModelRef
    vision: ModelRef
    enabled: bool = True
    #: Extra instruction appended to the vision model's prompt. CCR exposes
    #: this per-call as the tool's ``systemPrompt``; here it is configured
    #: once on the record because there is no per-call tool invocation.
    prompt: str = ""
    timeout_ms: int = DEFAULT_VISION_TIMEOUT_MS
    max_images: int = DEFAULT_MAX_IMAGES

    def summary(self) -> str:
        """One-line ``name = base + vision`` description.

        The CCR list view's columns (``virtualModelBaseModelSummary`` +
        ``virtualModelToolSummary``) collapsed into text for a terminal.
        """
        state = "" if self.enabled else "  (disabled)"
        return f"{self.name} = {self.base.selector} + vision:{self.vision.selector}{state}"

    def to_json(self) -> dict[str, Any]:
        """Serialize for the config file.

        Only non-default optional fields are written, so a hand-edited
        config stays readable and a future default change is picked up by
        records that never overrode it.
        """
        data: dict[str, Any] = {
            "name": self.name,
            "base": self.base.selector,
            "vision": self.vision.selector,
        }
        if not self.enabled:
            data["enabled"] = False
        if self.prompt:
            data["prompt"] = self.prompt
        if self.timeout_ms != DEFAULT_VISION_TIMEOUT_MS:
            data["timeoutMs"] = self.timeout_ms
        if self.max_images != DEFAULT_MAX_IMAGES:
            data["maxImages"] = self.max_images
        return data


def parse_selector(raw: object, *, field: str) -> ModelRef:
    """Parse ``provider:model`` into a :class:`ModelRef`.

    Splits on the FIRST colon so model ids that contain one survive
    (``openrouter:deepseek/deepseek-r1:free``). Mirrors ``/advisor``'s
    parse (``command_system/builtins.py``: ``raw.split(":", 1)``) including
    its "both halves non-empty" rule.

    ``field`` names the offending input in the error ("base", "vision") so
    a two-selector command can say which half was wrong.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise FusionModelError(f"{field} model is required (expected <provider>:<model>)")
    text = raw.strip()
    if ":" not in text:
        raise FusionModelError(
            f"{field} model must use <provider>:<model> syntax. Got: {text!r}. "
            "Example: deepseek:deepseek-v4-pro"
        )
    provider, model = text.split(":", 1)
    provider, model = provider.strip(), model.strip()
    if not provider or not model:
        raise FusionModelError(
            f"{field} model must use <provider>:<model> with both halves "
            f"non-empty. Got: {text!r}"
        )
    return ModelRef(provider=provider, model=model)


def _coerce_int(value: object, default: int) -> int:
    """Read an int from JSON that may hold a string (hand-edited config)."""
    if isinstance(value, bool):  # bool is an int subclass — not a count
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip())
        except ValueError:
            return default
        return parsed if parsed > 0 else default
    return default


def fusion_model_from_json(raw: object) -> FusionModel | None:
    """Rehydrate one record, or ``None`` if it is unusable.

    Returns ``None`` instead of raising so a single malformed entry in a
    hand-edited config cannot make every fusion model disappear —
    mirroring CCR's ``normalizeMcpServers``, which drops invalid entries
    and keeps the rest.
    """
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        base = parse_selector(raw.get("base"), field="base")
        vision = parse_selector(raw.get("vision"), field="vision")
    except FusionModelError:
        return None
    prompt = raw.get("prompt")
    return FusionModel(
        name=name.strip(),
        base=base,
        vision=vision,
        # Absent ⇒ enabled, matching CCR's ``profile.enabled !== false``.
        enabled=raw.get("enabled") is not False,
        prompt=prompt.strip() if isinstance(prompt, str) else "",
        timeout_ms=_coerce_int(raw.get("timeoutMs"), DEFAULT_VISION_TIMEOUT_MS),
        max_images=_coerce_int(raw.get("maxImages"), DEFAULT_MAX_IMAGES),
    )


def _manager():
    """The shared :class:`~src.config.ConfigManager`.

    Resolved through the module attribute at call time (not imported at
    module scope) so test isolation that re-points the config dir is
    honoured — the ``config.py`` ``_global_config_file`` discipline.
    """
    from src import config as cfg_mod

    return cfg_mod._get_default_manager()


def load_fusion_models() -> list[FusionModel]:
    """Every saved fusion model, including disabled ones.

    Global tier only — see the module docstring for why the merged tier is
    deliberately not consulted. Never raises: a broken config yields an
    empty list, because a fusion model is an enhancement and a config
    problem must not brick startup or the ``/model`` picker.

    Records are shape-validated (:func:`fusion_model_from_json`) but NOT
    re-run through :func:`validate_fusion_model`: the name-shadowing and
    vision-capability guards are **create-time only**. A hand-edited
    ``~/.clawcodex/config.json`` can therefore still register a fusion model
    named after another vendor's id and hijack ``/model <that id>``. That is
    self-inflicted rather than an attack surface — the file is the user's own
    and, being global-tier, no checked-out repo can contribute to it — and
    re-validating on every read would make a since-renamed provider silently
    drop working models.
    """
    try:
        raw = _manager().load_global().get(FUSION_MODELS_KEY)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    models: list[FusionModel] = []
    seen: set[str] = set()
    for entry in raw:
        model = fusion_model_from_json(entry)
        if model is None:
            continue
        key = model.name.lower()
        if key in seen:  # first writer wins, mirroring load order
            continue
        seen.add(key)
        models.append(model)
    return models


def save_fusion_models(models: list[FusionModel]) -> None:
    """Replace the saved set (global tier)."""
    _manager().set_global(FUSION_MODELS_KEY, [m.to_json() for m in models])


def get_fusion_model(name: object) -> FusionModel | None:
    """Look up a fusion model by name, case-insensitively.

    Case-insensitive because this is what decides whether a ``/model``
    argument is a fusion model, and model ids are routinely typed with
    inconsistent case (``GLM-5.2`` vs ``glm-5.2``).

    Returns disabled models too — callers that must not activate one
    check :attr:`FusionModel.enabled` themselves, so a disabled name
    reports "disabled" rather than "unknown".
    """
    if not isinstance(name, str) or not name.strip():
        return None
    key = name.strip().lower()
    for model in load_fusion_models():
        if model.name.lower() == key:
            return model
    return None


def is_fusion_model_name(name: object) -> bool:
    """Whether ``name`` is a saved, ENABLED fusion model.

    Stricter than :func:`get_fusion_model`, which also returns disabled
    records so a caller can report "disabled" rather than "unknown".

    The three routing paths (``set_model``, agent-server session init,
    headless) deliberately do NOT use this: each needs the record itself,
    and each distinguishes disabled-with-a-message from not-a-fusion-model.
    Kept as the public predicate for callers that only need the boolean.
    """
    model = get_fusion_model(name)
    return model is not None and model.enabled


def validate_fusion_model(
    model: FusionModel, *, existing: list[FusionModel] | None = None
) -> None:
    """Raise :class:`FusionModelError` if ``model`` cannot be saved.

    The CCR ``validateVirtualModelDraft`` analogue: name required, base
    required, vision required. Adds three checks CCR does not need because
    its virtual models live in a proxy's own namespace:

    * both providers must be **configured** (``/advisor``'s rule — an
      unknown provider key is a typo that would otherwise surface much
      later as a construction failure mid-turn),
    * the name must not collide with an existing fusion model, and
    * the name must not shadow a real model id on the base provider,
      which would make ``/model <name>`` ambiguous.
    """
    name = model.name.strip()
    if not name:
        raise FusionModelError("Fusion model name is required.")
    if ":" in name or any(ch.isspace() for ch in name):
        raise FusionModelError(
            f"Fusion model name may not contain ':' or whitespace. Got: {name!r}"
        )
    if model.base.selector == model.vision.selector:
        raise FusionModelError(
            f"Base and vision model are identical ({model.base.selector}). A fusion "
            "model borrows vision from a DIFFERENT model; select the base model "
            "directly instead."
        )

    configured = _configured_providers()
    for ref, field in ((model.base, "Base"), (model.vision, "Vision")):
        if configured and ref.provider not in configured:
            raise FusionModelError(
                f"{field} provider {ref.provider!r} is not configured. Configured: "
                f"{', '.join(configured)}. Add it in ~/.clawcodex/config.json or "
                "run `clawcodex login`."
            )

    # The base provider must NOT speak the Anthropic wire. Six live call
    # sites branch on ``isinstance(provider, AnthropicProvider)`` — prompt
    # caching (state/cache_state.py), deferred tools (query/query.py), the
    # goal judge, and the memory selector among them — and a delegating
    # wrapper is invisible to every one of them, so fusing an Anthropic
    # base would silently switch caching and tool deferral off. The
    # restriction costs nothing real: every Anthropic-wire model in the
    # registry already accepts images, so there is no text-only base there
    # to rescue. (Vision may be any provider — the vision model is called
    # directly, never wrapped.)
    from src.providers import provider_name_is_anthropic_wire

    if provider_name_is_anthropic_wire(model.base.provider):
        raise FusionModelError(
            f"Base provider {model.base.provider!r} speaks the Anthropic wire, whose "
            "models already accept images — a fusion model would add nothing and "
            "would disable prompt caching. Select the model directly with "
            "`/model` instead."
        )

    # The vision half must actually be able to see. ``supports_vision``
    # returns True for models absent from the table, so this rejects only
    # KNOWN text-only ids and stays permissive for everything else —
    # deliberately, since the table covers a fraction of what the providers
    # serve and a false rejection here would be worse than a late API error.
    #
    # This is the check that catches the feature's most inviting mistake:
    # picking a strong text model as the vision half because its family has
    # a multimodal sibling. glm-5.2 is exactly that trap (see configs.py).
    if not _vision_model_can_see(model.vision.model):
        raise FusionModelError(
            f"Vision model {model.vision.selector!r} does not support image input, "
            "so it cannot serve as the vision half of a fusion model. Pick a "
            "multimodal model (for Z.ai that means the *v family — glm-4.5v, "
            "glm-5v-turbo — not the glm-5.x text line)."
        )

    pool = load_fusion_models() if existing is None else existing
    if any(m.name.lower() == name.lower() for m in pool):
        raise FusionModelError(
            f"A fusion model named {name!r} already exists. Delete it first "
            f"(`/fusion delete {name}`) or pick another name."
        )
    shadowed = _shadowed_provider(name)
    if shadowed:
        raise FusionModelError(
            f"{name!r} is already a real model on provider {shadowed!r} — pick a "
            f"distinct name so `/model {name}` is unambiguous (e.g. "
            f"{suggest_fusion_name(model.base.model)})."
        )


def _vision_model_can_see(model_id: str) -> bool:
    """Whether ``model_id`` accepts image input, across id spellings.

    ``supports_vision`` matches the model table exactly, but the same model
    reaches clawcodex under several spellings: direct (``glm-5.2``),
    vendor-prefixed through a proxy (``z-ai/glm-5.2`` on OpenRouter), and
    with a routing suffix (``…:free``). Checking only the literal string let
    the provider-prefixed form through — and OpenRouter is the provider the
    ``/fusion`` help text uses in its own example, so that was the likely
    spelling for the very mistake this guard exists to catch.

    False only when SOME spelling is known text-only; unknown ids stay
    permissive, as in :func:`src.models.capabilities.supports_vision`.
    """
    from src.models.capabilities import supports_vision

    raw = (model_id or "").strip()
    candidates = {raw}
    # Strip a routing suffix (OpenRouter's ``:free`` / ``:nitro``).
    head = raw.split(":", 1)[0]
    candidates.add(head)
    # Strip a vendor prefix (``z-ai/glm-5.2`` -> ``glm-5.2``).
    for value in list(candidates):
        if "/" in value:
            candidates.add(value.rsplit("/", 1)[-1])
    return all(supports_vision(c) for c in candidates if c)


def _configured_providers() -> list[str]:
    """Provider keys present in the global config.

    Empty on failure, and callers treat empty as "skip the check" — the
    ``/advisor`` precedent, so a config read problem does not block a
    valid selector.
    """
    try:
        providers = _manager().load_global().get("providers")
    except Exception:
        return []
    return sorted(providers.keys()) if isinstance(providers, dict) else []


def _shadowed_provider(name: str) -> str | None:
    """The provider on which ``name`` is already a real model id, if any.

    Checked across EVERY provider, not just the fusion model's own base.
    The fusion lookup in ``agent_server._do_set_model`` runs before the
    "model X expects provider Y" guard and ignores the requested provider,
    so a fusion model named after another vendor's id would hijack it: with
    a fusion model called ``claude-opus-5``, ``/model claude-opus-5`` on an
    Anthropic session would silently route the turn to the fusion's base
    provider instead.

    Uses the static registry (``PROVIDER_INFO``) rather than a live
    ``get_available_models()`` call: validation runs while the user is
    typing and must not make a network round trip. A model that appears
    only via live discovery is therefore not caught — the cost is an
    ambiguous name the user chose, not a silent cross-vendor reroute.
    """
    try:
        from src.providers import PROVIDER_INFO

        lowered = name.lower()
        for provider_name, info in PROVIDER_INFO.items():
            for candidate in info.get("available_models", ()):
                if str(candidate).lower() == lowered:
                    return provider_name
    except Exception:
        return None
    return None


def suggest_fusion_name(base_model: str) -> str:
    """Suggest a fusion name for ``base_model``.

    Follows CCR's "use names that make the capability source obvious"
    convention (``GLM-5.2 + GLM-5V-Turbo = GLM-5.2V``): append ``V`` to
    the base model's id.
    """
    stem = (base_model or "model").strip().rsplit("/", 1)[-1]
    return f"{stem}-{VISION_NAME_SUFFIX}"


def create_fusion_model(
    name: str,
    base: str,
    vision: str,
    *,
    prompt: str = "",
    timeout_ms: int = DEFAULT_VISION_TIMEOUT_MS,
    max_images: int = DEFAULT_MAX_IMAGES,
) -> FusionModel:
    """Validate and save a new fusion model. Returns the saved record.

    ``base`` / ``vision`` are ``provider:model`` selectors. Raises
    :class:`FusionModelError` on any validation failure, leaving the
    config untouched.
    """
    model = FusionModel(
        name=(name or "").strip(),
        base=parse_selector(base, field="base"),
        vision=parse_selector(vision, field="vision"),
        prompt=(prompt or "").strip(),
        timeout_ms=_coerce_int(timeout_ms, DEFAULT_VISION_TIMEOUT_MS),
        max_images=_coerce_int(max_images, DEFAULT_MAX_IMAGES),
    )
    existing = load_fusion_models()
    validate_fusion_model(model, existing=existing)
    save_fusion_models([*existing, model])
    return model


def _forget_persisted_selection(name: str) -> None:
    """Clear ``settings.model`` if it names ``name``.

    A fusion model can be the persisted ``/model`` choice
    (``settings.get_persisted_model`` restores it at startup). Deleting or
    disabling it would otherwise leave a DANGLING name behind: the restore
    reads it, finds no fusion record, and falls through to the plain-model
    branch — putting a string that is not a real model id on the wire, for a
    400 on the next launch.

    Clearing at the mutation is the narrow fix. It cannot cover a config
    hand-edited to remove the record directly; that residue degrades to a
    clear API error rather than silent misbehaviour, the same bounded
    caveat as the create-time-only shadow guard.

    Best-effort: failing to clear must not fail the delete the user asked
    for.

    Writes through ``state.app_state._persist_settings_keys`` — the same
    helper the ``/model`` write side uses — rather than hand-rolling the
    load/mutate/save/invalidate sequence. Hand-rolling it would silently
    stop clearing if the persistence format ever moved, and the dangling
    name would come back. ``model=""`` + ``model_provider=""`` is that
    module's unset convention, so clear and set stay symmetric.
    """
    try:
        from src.settings.settings import get_settings
        from src.state.app_state import _persist_settings_keys

        current = str(getattr(get_settings(), "model", "") or "").strip()
        if current.lower() != name.strip().lower():
            return
        _persist_settings_keys(model="", model_provider="")
    except Exception:  # noqa: BLE001
        pass


def delete_fusion_model(name: str) -> FusionModel:
    """Remove a fusion model by name. Returns the removed record."""
    existing = load_fusion_models()
    key = (name or "").strip().lower()
    kept = [m for m in existing if m.name.lower() != key]
    if len(kept) == len(existing):
        raise FusionModelError(_unknown_message(name))
    removed = next(m for m in existing if m.name.lower() == key)
    save_fusion_models(kept)
    _forget_persisted_selection(removed.name)
    return removed


def set_fusion_model_enabled(name: str, enabled: bool) -> FusionModel:
    """Enable or disable a fusion model. Returns the updated record.

    The CCR list view's per-row toggle (``setVirtualModelEnabled``);
    disabling keeps the configuration so it can be turned back on without
    retyping both selectors.
    """
    existing = load_fusion_models()
    key = (name or "").strip().lower()
    updated: FusionModel | None = None
    out: list[FusionModel] = []
    for model in existing:
        if model.name.lower() == key:
            updated = replace(model, enabled=enabled)
            out.append(updated)
        else:
            out.append(model)
    if updated is None:
        raise FusionModelError(_unknown_message(name))
    save_fusion_models(out)
    return updated


def _unknown_message(name: object) -> str:
    known = [m.name for m in load_fusion_models()]
    listing = f" Known: {', '.join(known)}." if known else " No fusion models are saved."
    return f"No fusion model named {str(name).strip()!r}.{listing}"


__all__ = [
    "DEFAULT_MAX_IMAGES",
    "DEFAULT_VISION_TIMEOUT_MS",
    "FUSION_MODELS_KEY",
    "FusionModel",
    "FusionModelError",
    "ModelRef",
    "create_fusion_model",
    "delete_fusion_model",
    "fusion_model_from_json",
    "get_fusion_model",
    "is_fusion_model_name",
    "load_fusion_models",
    "parse_selector",
    "save_fusion_models",
    "set_fusion_model_enabled",
    "suggest_fusion_name",
    "validate_fusion_model",
]

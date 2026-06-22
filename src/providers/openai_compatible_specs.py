"""Data-driven registry of OpenAI-compatible LLM providers.

Most LLM vendors expose an OpenAI-style ``/v1/chat/completions`` API, so they
differ only in their default base URL, default model, accepted API-key env
vars, and display label — not in request/response handling. Rather than ship a
near-identical hand-written class per vendor, this module captures that
metadata as :class:`ProviderSpec` rows and synthesizes a concrete
:class:`~src.providers.openai_compatible.OpenAICompatibleProvider` subclass for
each one on demand.

Each row records the base URL, default model, accepted API-key env vars, and
aliases for one vendor; these are the vendors' own published defaults, so a
user pointing at any of them gets a working configuration out of the box.

Providers with bespoke behaviour are intentionally NOT in this table and keep
their hand-written classes:

* ``anthropic`` / ``minimax`` — native Anthropic Messages wire format.
* ``deepseek`` — prompt-prefix-cache usage re-mapping (``is_deepseek``).
* ``zai`` — GLM model-id canonicalization.
* ``openrouter`` — optional ranking/attribution headers.
* ``openai`` / ``gemini`` — kept as explicit classes (stable test import paths).

An OpenAI Codex / ChatGPT provider is intentionally excluded: it speaks the
OpenAI *Responses* API over a ChatGPT OAuth token, which is a separate wire
format ClawCodex's OpenAI-compatible layer does not implement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Optional

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from .openai_compatible import OpenAICompatibleProvider


@dataclass(frozen=True)
class ProviderSpec:
    """Static metadata describing one OpenAI-compatible provider."""

    #: Canonical provider id — the value users pass to ``--provider`` and the
    #: key under ``config.json`` ``providers.<id>``. Lowercase, hyphenated.
    id: str
    #: Human-readable label for login prompts / tables.
    label: str
    #: Default API base URL when config/CLI supplies none.
    default_base_url: str
    #: Default model when config/CLI supplies none.
    default_model: str
    #: Models surfaced in the login flow / pickers. Free-text model ids are
    #: always accepted regardless of this list; it is a convenience only.
    available_models: tuple[str, ...]
    #: API-key environment-variable candidates, highest precedence first.
    #: Used by ``src.providers.resolve_api_key`` to source a key when the
    #: provider's ``config.json`` ``api_key`` is empty.
    env_vars: tuple[str, ...]
    #: Alternate spellings accepted during provider resolution.
    aliases: tuple[str, ...] = ()
    #: Whether a key is mandatory. ``False`` for local servers (Ollama, vLLM,
    #: SGLang) that accept any/no token — these stay usable without ``login``.
    requires_api_key: bool = True
    #: Generated subclass name (for repr / debugging). Derived from ``id`` when
    #: omitted.
    class_name: str = ""

    def resolved_class_name(self) -> str:
        if self.class_name:
            return self.class_name
        parts = [p for p in self.id.replace("-", "_").split("_") if p]
        return "".join(p.capitalize() for p in parts) + "Provider"


# DeepSeek model ids served by the various OpenAI-compatible gateways, in the
# id spelling each gateway expects. ClawCodex is DeepSeek-focused, so most of
# these providers default to the DeepSeek model the gateway serves; every
# default below is the vendor's own published model/base-URL.
_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="nvidia-nim",
        label="NVIDIA NIM",
        default_base_url="https://integrate.api.nvidia.com/v1",
        default_model="deepseek-ai/deepseek-v4-pro",
        available_models=(
            "deepseek-ai/deepseek-v4-pro",
            "deepseek-ai/deepseek-v4-flash",
        ),
        env_vars=("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "DEEPSEEK_API_KEY"),
        aliases=("nvidia", "nvidia_nim", "nim"),
    ),
    ProviderSpec(
        id="atlascloud",
        label="AtlasCloud",
        default_base_url="https://api.atlascloud.ai/v1",
        default_model="deepseek-ai/deepseek-v4-flash",
        available_models=(
            "deepseek-ai/deepseek-v4-flash",
            "deepseek-ai/deepseek-v4-pro",
        ),
        env_vars=("ATLASCLOUD_API_KEY",),
        aliases=("atlas-cloud", "atlas_cloud", "atlas"),
    ),
    ProviderSpec(
        id="wanjie-ark",
        label="Wanjie Ark",
        default_base_url="https://maas-openapi.wanjiedata.com/api/v1",
        default_model="deepseek-reasoner",
        available_models=("deepseek-reasoner", "deepseek-chat"),
        env_vars=("WANJIE_ARK_API_KEY", "WANJIE_API_KEY", "WANJIE_MAAS_API_KEY"),
        aliases=(
            "wanjie",
            "wanjie_ark",
            "ark-wanjie",
            "ark_wanjie",
            "wanjieark",
            "wanjie-maas",
            "wanjie_maas",
            "wanjiemaas",
        ),
    ),
    ProviderSpec(
        id="volcengine",
        label="Volcengine Ark",
        default_base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        default_model="DeepSeek-V4-Pro",
        available_models=("DeepSeek-V4-Pro", "DeepSeek-V4-Flash"),
        env_vars=("VOLCENGINE_API_KEY", "VOLCENGINE_ARK_API_KEY", "ARK_API_KEY"),
        aliases=(
            "volcengine-ark",
            "volcengine_ark",
            "ark",
            "volc-ark",
            "volcengineark",
        ),
    ),
    ProviderSpec(
        id="xiaomi-mimo",
        label="Xiaomi MiMo",
        default_base_url="https://token-plan-sgp.xiaomimimo.com/v1",
        default_model="mimo-v2.5-pro",
        available_models=("mimo-v2.5-pro",),
        env_vars=(
            "XIAOMI_MIMO_TOKEN_PLAN_API_KEY",
            "MIMO_TOKEN_PLAN_API_KEY",
            "XIAOMI_MIMO_API_KEY",
            "XIAOMI_API_KEY",
            "MIMO_API_KEY",
        ),
        aliases=("xiaomi_mimo", "xiaomimimo", "mimo", "xiaomi"),
    ),
    ProviderSpec(
        id="novita",
        label="Novita AI",
        default_base_url="https://api.novita.ai/openai/v1",
        default_model="deepseek/deepseek-v4-pro",
        available_models=(
            "deepseek/deepseek-v4-pro",
            "deepseek/deepseek-v4-flash",
        ),
        env_vars=("NOVITA_API_KEY",),
    ),
    ProviderSpec(
        id="fireworks",
        label="Fireworks AI",
        default_base_url="https://api.fireworks.ai/inference/v1",
        default_model="accounts/fireworks/models/deepseek-v4-pro",
        available_models=("accounts/fireworks/models/deepseek-v4-pro",),
        env_vars=("FIREWORKS_API_KEY",),
        aliases=("fireworks-ai",),
    ),
    ProviderSpec(
        id="siliconflow",
        label="SiliconFlow",
        default_base_url="https://api.siliconflow.com/v1",
        default_model="deepseek-ai/DeepSeek-V4-Pro",
        available_models=(
            "deepseek-ai/DeepSeek-V4-Pro",
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
        env_vars=("SILICONFLOW_API_KEY",),
        aliases=("silicon-flow", "silicon_flow"),
    ),
    ProviderSpec(
        id="siliconflow-cn",
        label="SiliconFlow (China)",
        default_base_url="https://api.siliconflow.cn/v1",
        default_model="deepseek-ai/DeepSeek-V4-Pro",
        available_models=(
            "deepseek-ai/DeepSeek-V4-Pro",
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
        env_vars=("SILICONFLOW_API_KEY",),
        aliases=(
            "siliconflow-CN",
            "silicon-flow-cn",
            "silicon-flow-CN",
            "silicon_flow_cn",
            "silicon_flow_CN",
            "siliconflow-china",
        ),
    ),
    ProviderSpec(
        id="arcee",
        label="Arcee AI",
        default_base_url="https://api.arcee.ai/api/v1",
        default_model="trinity-large-thinking",
        available_models=("trinity-large-thinking",),
        env_vars=("ARCEE_API_KEY",),
        aliases=("arcee-ai", "arcee_ai"),
    ),
    ProviderSpec(
        id="moonshot",
        label="Moonshot / Kimi",
        default_base_url="https://api.moonshot.ai/v1",
        default_model="kimi-k2.7-code",
        available_models=("kimi-k2.7-code",),
        env_vars=("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        aliases=("moonshot-ai", "kimi", "kimi-k2"),
    ),
    ProviderSpec(
        id="sglang",
        label="SGLang (local)",
        default_base_url="http://localhost:30000/v1",
        default_model="deepseek-ai/DeepSeek-V4-Pro",
        available_models=(
            "deepseek-ai/DeepSeek-V4-Pro",
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
        env_vars=("SGLANG_API_KEY",),
        aliases=("sg-lang",),
        requires_api_key=False,
    ),
    ProviderSpec(
        id="vllm",
        label="vLLM (local)",
        default_base_url="http://localhost:8000/v1",
        default_model="deepseek-ai/DeepSeek-V4-Pro",
        available_models=(
            "deepseek-ai/DeepSeek-V4-Pro",
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
        env_vars=("VLLM_API_KEY",),
        aliases=("v-llm",),
        requires_api_key=False,
    ),
    ProviderSpec(
        id="ollama",
        label="Ollama (local)",
        default_base_url="http://localhost:11434/v1",
        default_model="deepseek-coder:1.3b",
        available_models=("deepseek-coder:1.3b",),
        env_vars=("OLLAMA_API_KEY",),
        aliases=("ollama-local",),
        requires_api_key=False,
    ),
    ProviderSpec(
        id="huggingface",
        label="Hugging Face",
        default_base_url="https://router.huggingface.co/v1",
        default_model="deepseek-ai/DeepSeek-V4-Pro",
        available_models=(
            "deepseek-ai/DeepSeek-V4-Pro",
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
        env_vars=("HUGGINGFACE_API_KEY", "HF_TOKEN"),
        aliases=("hugging-face", "hugging_face", "hf"),
    ),
    ProviderSpec(
        id="together",
        label="Together AI",
        default_base_url="https://api.together.xyz/v1",
        default_model="deepseek-ai/DeepSeek-V4-Pro",
        available_models=(
            "deepseek-ai/DeepSeek-V4-Pro",
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
        env_vars=("TOGETHER_API_KEY",),
        aliases=("together-ai", "together_ai"),
    ),
    ProviderSpec(
        id="stepfun",
        label="StepFun / StepFlash",
        default_base_url="https://api.stepfun.ai/v1",
        default_model="step-3.7-flash",
        available_models=("step-3.7-flash",),
        env_vars=("STEPFUN_API_KEY", "STEP_API_KEY"),
        aliases=("step-fun", "step_fun", "stepflash", "step-flash", "step_flash"),
    ),
    ProviderSpec(
        id="deepinfra",
        label="DeepInfra",
        default_base_url="https://api.deepinfra.com/v1/openai",
        default_model="deepseek-ai/DeepSeek-V4-Pro",
        available_models=(
            "deepseek-ai/DeepSeek-V4-Pro",
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
        env_vars=("DEEPINFRA_API_KEY", "DEEPINFRA_TOKEN"),
        aliases=("deep-infra", "deep_infra"),
    ),
)


#: Canonical id -> spec.
SPECS_BY_ID: dict[str, ProviderSpec] = {spec.id: spec for spec in _SPECS}


class _SpecOpenAICompatibleProvider(OpenAICompatibleProvider):
    """Base for registry-generated providers; ``SPEC`` is set per subclass.

    All request/response handling (message translation, tool-call rebuild,
    streaming + ESC abort, bounded read timeout) is inherited unchanged from
    :class:`OpenAICompatibleProvider`; only the SDK client construction and the
    model list vary, and both read from ``SPEC``.
    """

    SPEC: ClassVar[ProviderSpec]

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        spec = self.SPEC
        super().__init__(
            api_key,
            base_url or spec.default_base_url,
            model or spec.default_model,
        )

    def _create_client(self) -> Any:
        """Create an OpenAI SDK client pointed at this provider's base URL.

        Mirrors the hand-written providers (DeepSeek / OpenRouter / Z.ai): the
        bounded read timeout is applied centrally by
        ``OpenAICompatibleProvider.client``; the optional ``CLAWCODEX_SSL_VERIFY``
        bypass is honoured here for corporate/self-hosted endpoints.
        """
        if OpenAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "openai package is not installed. Install optional dependencies "
                f"to use {type(self).__name__}."
            )
        import os

        # Local servers (Ollama / vLLM / SGLang) accept any token; pass a
        # placeholder rather than letting an empty key fall through to the SDK's
        # OPENAI_API_KEY env lookup (which would silently target the wrong host).
        api_key = self.api_key or "EMPTY"
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": self.base_url or self.SPEC.default_base_url,
        }
        if os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
            import httpx

            kwargs["http_client"] = httpx.Client(verify=False)
        return OpenAI(**kwargs)

    def get_available_models(self) -> list[str]:
        return list(self.SPEC.available_models)


# Generated subclasses are cached so repeated lookups return the same class
# object (stable identity for ``is`` checks / isinstance and cheaper repeats).
_CLASS_CACHE: dict[str, type[_SpecOpenAICompatibleProvider]] = {}


def build_provider_class(provider_id: str) -> type[_SpecOpenAICompatibleProvider]:
    """Return the generated provider class for ``provider_id`` (canonical id).

    Raises ``KeyError`` if ``provider_id`` is not a registry provider.
    """
    spec = SPECS_BY_ID[provider_id]
    cached = _CLASS_CACHE.get(provider_id)
    if cached is not None:
        return cached
    cls = type(
        spec.resolved_class_name(),
        (_SpecOpenAICompatibleProvider,),
        {
            "SPEC": spec,
            "DEFAULT_BASE_URL": spec.default_base_url,
            "DEFAULT_MODEL": spec.default_model,
            "__doc__": (
                f"{spec.label} provider (OpenAI-compatible). Generated from "
                "ProviderSpec; see src/providers/openai_compatible_specs.py."
            ),
        },
    )
    _CLASS_CACHE[provider_id] = cls
    return cls


def spec_provider_info() -> dict[str, dict[str, Any]]:
    """Registry rows as ``PROVIDER_INFO``-shaped metadata dicts."""
    return {
        spec.id: {
            "label": spec.label,
            "default_base_url": spec.default_base_url,
            "default_model": spec.default_model,
            "available_models": list(spec.available_models),
        }
        for spec in _SPECS
    }


def spec_aliases() -> dict[str, str]:
    """Alias spelling -> canonical id, for every registry provider."""
    aliases: dict[str, str] = {}
    for spec in _SPECS:
        for alias in spec.aliases:
            aliases[alias] = spec.id
    return aliases


__all__ = [
    "ProviderSpec",
    "SPECS_BY_ID",
    "build_provider_class",
    "spec_provider_info",
    "spec_aliases",
]

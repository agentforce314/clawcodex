"""The OpenAI-compatible vendors ported from OpenCode's profile table.

OpenCode enumerates its OpenAI-compatible providers in
``packages/llm/src/providers/openai-compatible-profile.ts``. Four of them had
no row here: groq, cerebras, baseten and xai. They are plain
``/chat/completions`` vendors, so they need no code — only correct metadata,
which is exactly what is easy to get wrong and what these tests pin.

Model ids did not come from OpenCode, because its profile table has none to
give: ``OpenAICompatibleProfile`` is ``{provider, baseURL}`` and nothing else.
They were read from each vendor's own docs; `test_provider_registry` pins
those values as the guard against drift.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.providers import PROVIDER_INFO, get_provider_class
from src.providers.openai_compatible_specs import SPECS_BY_ID, _SPECS

# ``moonshot`` is not an OpenCode port, but it became a hosted hybrid row when
# kimi-k3 was registered, so it belongs to exactly the population these
# parameterized guards describe — curated list leads, discovery appends,
# vendor env var sources the key.
PORTED = ("groq", "cerebras", "baseten", "xai", "moonshot")


@pytest.mark.parametrize("provider_id", PORTED)
def test_the_provider_is_registered_and_usable(provider_id: str) -> None:
    assert provider_id in SPECS_BY_ID
    assert provider_id in PROVIDER_INFO
    provider = get_provider_class(provider_id)(api_key="sk-test")
    assert provider.base_url == SPECS_BY_ID[provider_id].default_base_url
    assert provider.model == SPECS_BY_ID[provider_id].default_model


@pytest.mark.parametrize("provider_id", PORTED)
def test_discovery_extends_the_curated_list_instead_of_replacing_it(
    provider_id: str,
) -> None:
    """Curation survives a live `/models` read; discovery only appends.

    These are HOSTED vendors, so the default `dynamic` mode is wrong for
    them: it means "discovered REPLACES static", which is right for a local
    sglang/vllm/ollama server whose static ids are placeholder stubs, and
    backwards for a vendor whose raw catalog also lists speech, moderation
    and embedding models. Under `dynamic` the curated ids vanished from the
    picker entirely and ASR/TTS models took their place.
    """
    import src.providers.model_discovery as discovery

    spec = SPECS_BY_ID[provider_id]
    discovered = ["whisper-large-v3", "some-embedding-model"]

    # Driven through the provider, NOT through `_merge` directly. Asserting
    # `spec.catalog_mode == "hybrid"` and `_merge("hybrid", ...)` separately
    # tests both ENDS of the fix and never the wire between them: deleting
    # `mode=spec.catalog_mode` from the discovery call left the whole suite
    # green while curation was destroyed at runtime.
    #
    # The cache dir is pinned ONCE for the call. Building a fresh temp dir
    # per invocation would hand every call an empty cache and fake a pass.
    with tempfile.TemporaryDirectory() as cache_dir:
        cache_file = Path(cache_dir) / "model-discovery-cache.json"
        with patch.object(discovery, "_cache_path", lambda: cache_file):
            # Seeded rather than fetched: `discovered_models` refreshes in the
            # BACKGROUND and returns immediately, so a patched fetch would not
            # have landed by the time the call returns. Priming the cache
            # exercises the same merge path deterministically.
            key = discovery._cache_key(provider_id, spec.default_base_url)
            discovery._write_cache(
                {key: {"models": discovered, "fetched_at": time.time()}}
            )
            provider = get_provider_class(provider_id)(api_key="sk-test")
            models = provider.get_available_models()

    curated = list(spec.available_models)
    assert models[: len(curated)] == curated, (
        f"curation did not survive discovery: {models}"
    )
    assert "whisper-large-v3" in models, "discovered models were not appended"


@pytest.mark.parametrize("provider_id", PORTED)
def test_the_default_model_is_one_of_the_offered_models(provider_id: str) -> None:
    spec = SPECS_BY_ID[provider_id]
    assert spec.default_model in spec.available_models


@pytest.mark.parametrize("provider_id", PORTED)
def test_the_key_is_sourced_from_the_vendor_env_var(provider_id: str) -> None:
    """Each vendor's conventional variable, so an existing shell just works."""
    expected = {
        "groq": "GROQ_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "baseten": "BASETEN_API_KEY",
        "xai": "XAI_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
    }[provider_id]
    assert expected in SPECS_BY_ID[provider_id].env_vars


def test_no_id_or_alias_collides_across_the_whole_registry() -> None:
    """A collision would silently shadow an existing provider.

    Spans the FULL namespace, not just the spec table. Seven providers are
    hand-written and absent from ``_SPECS`` — anthropic, deepseek, gemini,
    minimax, openai, openrouter, zai — so a table-only check cannot see a new
    alias shadowing one of them, which is precisely the collision the new
    rows are most able to cause (`grok`, `base-ten`, `groqcloud`).
    """
    from src.providers import PROVIDER_ALIASES

    # Seeded from ids AND the hand-written alias map: PROVIDER_ALIASES holds
    # names like `glm` / `z.ai` that are aliases rather than ids, so an
    # id-only seed left them shadowable (a mutant adding `glm` as an xai
    # alias survived). `setdefault` means the hand-written entry wins, so
    # such a collision leaves the NEW alias silently dead rather than
    # hijacking the old one — still a bug, just a quiet one.
    seen: dict[str, str] = {pid: pid for pid in PROVIDER_INFO}
    for alias, target in PROVIDER_ALIASES.items():
        seen.setdefault(alias, target)
    collisions = []
    for spec in _SPECS:
        for name in (spec.id, *(spec.aliases or ())):
            owner = seen.get(name)
            if owner is not None and owner != spec.id:
                collisions.append((name, owner, spec.id))
            seen[name] = spec.id
    assert not collisions, f"name collisions: {collisions}"


def test_xai_requests_go_to_chat_completions() -> None:
    """OpenCode defaults xai to the Responses protocol; this row does not.

    Asserts the URL actually requested, not the provider's type. An earlier
    version of this test checked ``not hasattr(provider, "_use_responses")``,
    which holds for 29 of the 30 registered providers — it restated "xai is
    not OpenAIProvider", something ``get_provider_class`` already guarantees,
    and stayed green when the row was mutated to declare Responses.

    The reason for the departure is structural, not just an absence of
    evidence: ``openai_responses`` is imported only by ``openai_provider``,
    and ``_use_responses`` is gated behind ``_is_first_party_base_url()``,
    which #783 scoped to api.openai.com. Routing xai over Responses is not a
    row change — it needs a hand-written class and a carve-out in that gate.
    """
    seen: dict[str, object] = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "x",
                "choices": [{"message": {"role": "assistant", "content": "hi"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    provider = get_provider_class("xai")(api_key="sk-test")

    import httpx

    def _spy(self, request, **kwargs):  # pragma: no cover - exercised below
        seen["url"] = str(request.url)
        raise RuntimeError("stop-before-network")

    with patch.object(httpx.Client, "send", _spy):
        try:
            provider.chat([{"role": "user", "content": "hi"}])
        except Exception:
            pass

    url = str(seen.get("url", ""))
    assert url, "no HTTP request was attempted"
    assert url.endswith("/chat/completions"), url
    assert "/responses" not in url


# --- model-config resolution --------------------------------------------


@pytest.mark.parametrize(
    "model_id", ["gpt-oss-120b", "gpt-oss-20b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
)
def test_gpt_oss_does_not_inherit_a_gpt_5_config(model_id: str) -> None:
    """`gpt-oss-*` must resolve to itself, never to the gpt-5.x family.

    `get_model_config` falls back to a prefix match on `key.rsplit("-", 1)[0]`,
    under which `gpt-oss-120b` reduces to `gpt` and collided with `gpt-5.5` —
    silently inheriting a 272k context window, a 128k output cap and $3/$15
    pricing. The window is the dangerous one: it sizes auto-compaction, so a
    session would sail past the real 131k limit and die on a context-length
    400 instead of compacting.

    Cerebras's default model is a bare `gpt-oss-120b`, which is how this
    became reachable. The namespaced `openai/gpt-oss-120b` that groq and
    baseten serve dodged the prefix but fell to the generic 200k default —
    also larger than the truth, and so the same failure more slowly.
    """
    from src.models.configs import get_model_config

    config = get_model_config(model_id)
    assert config is not None, f"{model_id} has no config row"
    assert "gpt-5" not in config.model_id, (
        f"{model_id} resolved to {config.model_id}"
    )
    assert config.context_window == 131_072, config.context_window


def test_every_ported_default_model_resolves_to_itself_or_to_nothing() -> None:
    """No new default may borrow another model's limits.

    A row whose default silently resolves to a different model inherits that
    model's context window, which is a correctness bug rather than a cosmetic
    one. `None` is fine — the caller then applies conservative defaults.
    """
    from src.models.configs import get_model_config

    for provider_id in PORTED:
        default = SPECS_BY_ID[provider_id].default_model
        config = get_model_config(default)
        if config is None:
            continue
        assert config.model_id == default, (
            f"{provider_id}: default {default!r} resolved to {config.model_id!r}"
        )


def test_local_server_rows_keep_the_replace_semantics() -> None:
    """The asymmetry `catalog_mode` introduces, guarded from both sides.

    sglang/vllm/ollama ship placeholder ids (`deepseek-coder:1.3b`), and the
    endpoint is the only truth — `dynamic` is what actually removes the stub
    once a real list arrives. Flipping the field's default to `hybrid` would
    make those stubs permanent while leaving every hosted row unchanged, so
    the hosted tests alone cannot see it.
    """
    for provider_id in ("ollama", "vllm", "sglang"):
        assert SPECS_BY_ID[provider_id].catalog_mode == "dynamic", provider_id

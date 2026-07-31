"""Fusion model record + lifecycle (create / configure / save / manage).

The global config these write to is isolated per test by the autouse
``_isolate_user_permission_settings`` fixture in ``tests/conftest.py``,
which repoints ``config.GLOBAL_CONFIG_DIR`` / ``GLOBAL_CONFIG_FILE`` at
``tmp_path`` — so nothing here touches the developer's real
``~/.clawcodex/config.json``.
"""

from __future__ import annotations

import pytest

from src.providers.fusion_models import (
    DEFAULT_MAX_IMAGES,
    DEFAULT_VISION_TIMEOUT_MS,
    FusionModel,
    FusionModelError,
    ModelRef,
    create_fusion_model,
    delete_fusion_model,
    fusion_model_from_json,
    get_fusion_model,
    is_fusion_model_name,
    load_fusion_models,
    parse_selector,
    save_fusion_models,
    set_fusion_model_enabled,
    suggest_fusion_name,
)

BASE = "deepseek:deepseek-v4-pro"
VISION = "openrouter:google/gemini-2.5-flash"


@pytest.fixture(autouse=True)
def _configured_providers(monkeypatch):
    """Give validation a providers block to check selectors against.

    ``validate_fusion_model`` skips the configured-provider check when the
    list is empty, so without this the "unknown provider" test would pass
    for the wrong reason.
    """
    import src.providers.fusion_models as mod

    monkeypatch.setattr(
        mod, "_configured_providers", lambda: ["deepseek", "openrouter", "zai", "anthropic"]
    )


# ── selector parsing ─────────────────────────────────────────────────────


def test_parse_selector_splits_on_first_colon_only():
    # An OpenRouter ``:free`` suffix must survive — splitting on the LAST
    # colon, or on every colon, would mangle it.
    ref = parse_selector("openrouter:deepseek/deepseek-r1:free", field="vision")
    assert ref.provider == "openrouter"
    assert ref.model == "deepseek/deepseek-r1:free"
    assert ref.selector == "openrouter:deepseek/deepseek-r1:free"


def test_parse_selector_trims_whitespace():
    ref = parse_selector("  deepseek : deepseek-v4-pro  ", field="base")
    assert (ref.provider, ref.model) == ("deepseek", "deepseek-v4-pro")


@pytest.mark.parametrize("bad", ["", "   ", "deepseek", "deepseek:", ":model", ":", None, 7])
def test_parse_selector_rejects_malformed(bad):
    with pytest.raises(FusionModelError):
        parse_selector(bad, field="base")


def test_parse_selector_names_the_offending_field():
    with pytest.raises(FusionModelError, match="vision"):
        parse_selector("nocolon", field="vision")


def test_suggest_fusion_name_follows_ccr_convention():
    # CCR: "GLM-5.2 + GLM-5V-Turbo = GLM-5.2V" — capability-obvious names.
    assert suggest_fusion_name("deepseek-v4-pro") == "deepseek-v4-pro-V"
    # A provider-qualified id keeps only the model stem.
    assert suggest_fusion_name("deepseek/deepseek-v4-pro") == "deepseek-v4-pro-V"


# ── create / save / load round-trip ──────────────────────────────────────


def test_create_saves_and_round_trips():
    created = create_fusion_model("dsv", BASE, VISION)
    assert created.name == "dsv"
    assert created.base == ModelRef("deepseek", "deepseek-v4-pro")
    assert created.vision == ModelRef("openrouter", "google/gemini-2.5-flash")
    assert created.enabled is True

    loaded = load_fusion_models()
    assert [m.name for m in loaded] == ["dsv"]
    assert loaded[0] == created


def test_to_json_omits_defaults():
    model = FusionModel(name="x", base=ModelRef("a", "b"), vision=ModelRef("c", "d"))
    assert model.to_json() == {"name": "x", "base": "a:b", "vision": "c:d"}


def test_to_json_includes_non_defaults():
    model = FusionModel(
        name="x",
        base=ModelRef("a", "b"),
        vision=ModelRef("c", "d"),
        enabled=False,
        prompt="focus on text",
        timeout_ms=1234,
        max_images=2,
    )
    assert model.to_json() == {
        "name": "x",
        "base": "a:b",
        "vision": "c:d",
        "enabled": False,
        "prompt": "focus on text",
        "timeoutMs": 1234,
        "maxImages": 2,
    }


def test_get_fusion_model_is_case_insensitive():
    create_fusion_model("DeepSeek-V", BASE, VISION)
    assert get_fusion_model("deepseek-v") is not None
    assert get_fusion_model("DEEPSEEK-V") is not None
    assert get_fusion_model("nope") is None


def test_is_fusion_model_name_requires_enabled():
    create_fusion_model("dsv", BASE, VISION)
    assert is_fusion_model_name("dsv") is True
    set_fusion_model_enabled("dsv", False)
    # get_fusion_model still finds it (so the caller can say "disabled"),
    # but the activation test must be False.
    assert get_fusion_model("dsv") is not None
    assert is_fusion_model_name("dsv") is False


# ── validation ───────────────────────────────────────────────────────────


def test_rejects_duplicate_name():
    create_fusion_model("dsv", BASE, VISION)
    with pytest.raises(FusionModelError, match="already exists"):
        create_fusion_model("dsv", BASE, VISION)


def test_rejects_duplicate_name_case_insensitively():
    create_fusion_model("dsv", BASE, VISION)
    with pytest.raises(FusionModelError, match="already exists"):
        create_fusion_model("DSV", BASE, VISION)


def test_rejects_unknown_provider():
    with pytest.raises(FusionModelError, match="not configured"):
        create_fusion_model("x", "nosuch:model", VISION)


def test_rejects_identical_base_and_vision():
    with pytest.raises(FusionModelError, match="identical"):
        create_fusion_model("x", BASE, BASE)


def test_rejects_name_with_colon_or_space():
    with pytest.raises(FusionModelError, match="may not contain"):
        create_fusion_model("a:b", BASE, VISION)


def test_rejects_name_shadowing_a_real_model():
    # ``deepseek-v4-pro`` is a real id on the deepseek provider, so reusing
    # it would make ``/model deepseek-v4-pro`` ambiguous.
    with pytest.raises(FusionModelError, match="already a real model"):
        create_fusion_model("deepseek-v4-pro", BASE, VISION)


@pytest.mark.parametrize("hijack", ["claude-opus-5", "gpt-5.4", "MiniMax-M3"])
def test_rejects_name_shadowing_ANOTHER_providers_model(hijack):
    # Cross-vendor hijack: the fusion lookup in _do_set_model runs BEFORE
    # the "model X expects provider Y" guard and ignores the requested
    # provider, so a fusion model named after another vendor's id would make
    # `/model claude-opus-5` on an Anthropic session silently route to the
    # fusion's base provider instead.
    with pytest.raises(FusionModelError, match="already a real model"):
        create_fusion_model(hijack, BASE, VISION)


def test_rejects_anthropic_wire_base():
    # Six live call sites branch on isinstance(provider, AnthropicProvider);
    # a wrapper is invisible to them, so an Anthropic base would silently
    # disable prompt caching and deferred tools.
    with pytest.raises(FusionModelError, match="Anthropic wire"):
        create_fusion_model("x", "anthropic:claude-3-haiku-20240307", VISION)


def test_rejects_known_text_only_vision_model():
    # The feature's most inviting mistake: glm-5.2 has multimodal siblings
    # but is itself text-only on both Z.ai endpoints (probed 2026-07-30).
    with pytest.raises(FusionModelError, match="does not support image input"):
        create_fusion_model("x", BASE, "zai:glm-5.2")


@pytest.mark.parametrize(
    "spelling",
    [
        "openrouter:z-ai/glm-5.2",     # vendor-prefixed through a proxy
        "openrouter:z-ai/glm-5.2:free",  # …plus a routing suffix
        "openrouter:glm-5.2",
    ],
)
def test_rejects_text_only_vision_model_in_any_spelling(spelling):
    # The same trap reached by the id form OpenRouter uses — and OpenRouter
    # is the provider /fusion's own help example names, so this was the
    # likely spelling for the mistake the guard exists to catch.
    with pytest.raises(FusionModelError, match="does not support image input"):
        create_fusion_model("x", BASE, spelling)


@pytest.mark.parametrize("vision", ["zai:glm-4.5v", "zai:glm-5v-turbo", "zai:glm-4.6v"])
def test_accepts_zai_vision_family(vision):
    # These are the models the rejection message tells users to pick, so
    # they MUST be accepted. (They are explicitly registered in
    # MODEL_CONFIGS, so this exercises the exact-hit path — the LOOKUP rule
    # that protects unregistered ids is gated by the test below.)
    model = create_fusion_model(f"x-{vision[-6:]}", BASE, vision)
    assert model.vision.selector == vision


@pytest.mark.parametrize("vision", ["zai:glm-6v", "zai:glm-5.4v-flash"])
def test_accepts_an_UNREGISTERED_glm_vision_model(vision):
    # The one that gates the lookup rule itself, and the reason the rule
    # exists rather than just registering the current *v models.
    #
    # ``get_model_config`` prefix-matches on ``key.rsplit("-", 1)[0]``, and
    # the ``glm-5.2`` row's base is bare ``glm`` — so EVERY ``glm*`` id
    # inherits that row unless it has an exact entry. A future Z.ai vision
    # model nobody has registered yet would therefore inherit
    # ``supports_vision=False`` and be refused, with an error message
    # recommending the *v family it belongs to.
    #
    # Verified by mutation: reverting ``supports_vision`` to trust a
    # prefix-inherited ``False`` fails THIS test and no other.
    from src.models.configs import MODEL_CONFIGS

    model_id = vision.split(":", 1)[1]
    assert model_id not in MODEL_CONFIGS, "pick an id that is genuinely unregistered"
    created = create_fusion_model(f"x-{model_id}", BASE, vision)
    assert created.vision.model == model_id


def test_allows_vision_model_absent_from_the_model_table():
    # supports_vision() defaults True for unknown ids, so the check is
    # permissive rather than an allowlist — a false rejection would be
    # worse than a late API error.
    model = create_fusion_model("x", BASE, "openrouter:some/brand-new-vlm")
    assert model.vision.model == "some/brand-new-vlm"


def test_failed_create_leaves_config_untouched():
    create_fusion_model("keep", BASE, VISION)
    with pytest.raises(FusionModelError):
        create_fusion_model("keep", BASE, VISION)
    assert [m.name for m in load_fusion_models()] == ["keep"]


# ── manage: delete / enable / disable ────────────────────────────────────


def test_delete_removes_only_the_named_model():
    create_fusion_model("a", BASE, VISION)
    create_fusion_model("b", "deepseek:deepseek-v4-flash", VISION)
    removed = delete_fusion_model("a")
    assert removed.name == "a"
    assert [m.name for m in load_fusion_models()] == ["b"]


def _persist_selection(model: str, provider: str = "deepseek") -> None:
    from src import config as cfg_mod
    from src.settings.settings import invalidate_settings_cache

    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    cfg["settings"] = {"model": model, "model_provider": provider}
    mgr.save_global(cfg)
    invalidate_settings_cache()


def test_persisted_fusion_model_overrides_the_configured_default_provider():
    # A fusion record names its own provider, so restoring it is meaningful
    # even when the session's DEFAULT provider is something else — that is
    # the point of having selected it.
    from src.settings.settings import get_persisted_model

    create_fusion_model("dsv", BASE, VISION)
    _persist_selection("dsv")
    assert get_persisted_model("anthropic") == "dsv"


def test_persisted_fusion_model_yields_to_an_EXPLICIT_provider():
    # Restoring a fusion model REPLACES the session provider with its base,
    # so honouring it over an explicit `--provider` would silently ignore
    # what the user just typed. Explicit intent wins.
    from src.settings.settings import get_persisted_model

    create_fusion_model("dsv", BASE, VISION)          # base is deepseek:…
    _persist_selection("dsv")
    assert get_persisted_model("openrouter", provider_is_explicit=True) == ""
    # …but an explicit provider that MATCHES the fusion's base still restores.
    assert get_persisted_model("deepseek", provider_is_explicit=True) == "dsv"


def test_delete_clears_a_persisted_selection_naming_it():
    # Otherwise the restore reads a dangling name, finds no fusion record,
    # falls through to the plain-model branch, and puts a string that is not
    # a real model id on the wire — a 400 on the next launch.
    from src import config as cfg_mod
    from src.settings.settings import get_persisted_model, invalidate_settings_cache

    create_fusion_model("dsv", BASE, VISION)
    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    cfg["settings"] = {"model": "dsv", "model_provider": "deepseek"}
    mgr.save_global(cfg)
    invalidate_settings_cache()
    assert get_persisted_model("deepseek") == "dsv"

    delete_fusion_model("dsv")
    invalidate_settings_cache()
    assert get_persisted_model("deepseek") == ""


def test_delete_leaves_an_unrelated_persisted_selection_alone():
    from src import config as cfg_mod
    from src.settings.settings import get_persisted_model, invalidate_settings_cache

    create_fusion_model("dsv", BASE, VISION)
    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    cfg["settings"] = {"model": "deepseek-v4-flash", "model_provider": "deepseek"}
    mgr.save_global(cfg)
    invalidate_settings_cache()

    delete_fusion_model("dsv")
    invalidate_settings_cache()
    assert get_persisted_model("deepseek") == "deepseek-v4-flash"


def test_disabled_fusion_model_resolves_to_nothing_without_clearing():
    # Disable does not dangle: the record still exists, so the restore side
    # sees `enabled=False` and declines. The configuration is preserved so
    # re-enabling restores the selection too.
    from src import config as cfg_mod
    from src.settings.settings import get_persisted_model, invalidate_settings_cache

    create_fusion_model("dsv", BASE, VISION)
    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    cfg["settings"] = {"model": "dsv", "model_provider": "deepseek"}
    mgr.save_global(cfg)
    invalidate_settings_cache()

    set_fusion_model_enabled("dsv", False)
    invalidate_settings_cache()
    assert get_persisted_model("deepseek") == ""

    set_fusion_model_enabled("dsv", True)
    invalidate_settings_cache()
    assert get_persisted_model("deepseek") == "dsv"


def test_delete_unknown_raises_and_lists_known():
    create_fusion_model("a", BASE, VISION)
    with pytest.raises(FusionModelError, match="Known: a"):
        delete_fusion_model("nope")


def test_disable_preserves_configuration():
    create_fusion_model("a", BASE, VISION)
    updated = set_fusion_model_enabled("a", False)
    assert updated.enabled is False
    reloaded = get_fusion_model("a")
    # The whole record survives, so re-enabling needs no retyping.
    assert reloaded.base.selector == BASE
    assert reloaded.vision.selector == VISION
    assert set_fusion_model_enabled("a", True).enabled is True


def test_summary_marks_disabled():
    create_fusion_model("a", BASE, VISION)
    assert "(disabled)" not in get_fusion_model("a").summary()
    set_fusion_model_enabled("a", False)
    assert "(disabled)" in get_fusion_model("a").summary()


# ── tolerating a hand-edited config ─────────────────────────────────────


def test_malformed_entry_is_dropped_not_fatal():
    good = FusionModel(name="ok", base=ModelRef("deepseek", "m"), vision=ModelRef("openrouter", "v"))
    save_fusion_models([good])
    # Splice junk in beside the good record, the way a hand edit would.
    from src import config as cfg_mod
    from src.providers.fusion_models import FUSION_MODELS_KEY

    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    cfg[FUSION_MODELS_KEY] = [
        "not-a-dict",
        {"name": "missing-halves"},
        {"base": "a:b", "vision": "c:d"},  # no name
        *cfg[FUSION_MODELS_KEY],
    ]
    mgr.save_global(cfg)

    # One bad record must not hide the rest.
    assert [m.name for m in load_fusion_models()] == ["ok"]


def test_non_list_value_yields_empty():
    from src import config as cfg_mod
    from src.providers.fusion_models import FUSION_MODELS_KEY

    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    cfg[FUSION_MODELS_KEY] = {"not": "a list"}
    mgr.save_global(cfg)
    assert load_fusion_models() == []


def test_duplicate_names_in_config_collapse_to_first():
    from src import config as cfg_mod
    from src.providers.fusion_models import FUSION_MODELS_KEY

    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    cfg[FUSION_MODELS_KEY] = [
        {"name": "dup", "base": "deepseek:first", "vision": "openrouter:v"},
        {"name": "DUP", "base": "deepseek:second", "vision": "openrouter:v"},
    ]
    mgr.save_global(cfg)
    loaded = load_fusion_models()
    assert len(loaded) == 1
    assert loaded[0].base.model == "first"


def test_from_json_absent_enabled_means_enabled():
    model = fusion_model_from_json({"name": "x", "base": "a:b", "vision": "c:d"})
    assert model.enabled is True


def test_from_json_coerces_string_numbers():
    model = fusion_model_from_json(
        {"name": "x", "base": "a:b", "vision": "c:d", "timeoutMs": "5000", "maxImages": "3"}
    )
    assert (model.timeout_ms, model.max_images) == (5000, 3)


@pytest.mark.parametrize("junk", ["abc", "", "0", "-1", True, None, [], 0])
def test_from_json_bad_numbers_fall_back_to_defaults(junk):
    model = fusion_model_from_json(
        {"name": "x", "base": "a:b", "vision": "c:d", "timeoutMs": junk, "maxImages": junk}
    )
    assert model.timeout_ms == DEFAULT_VISION_TIMEOUT_MS
    assert model.max_images == DEFAULT_MAX_IMAGES


def test_load_never_raises_when_config_is_unreadable(monkeypatch):
    import src.providers.fusion_models as mod

    def boom():
        raise RuntimeError("config on fire")

    monkeypatch.setattr(mod, "_manager", boom)
    # A fusion model is an enhancement; a config problem must not brick the
    # /model picker or startup.
    assert load_fusion_models() == []
    assert get_fusion_model("anything") is None
    assert is_fusion_model_name("anything") is False

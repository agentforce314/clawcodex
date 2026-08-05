"""Moonshot ``kimi-k3`` registration: window, pricing, effort vocabulary.

The model worked end-to-end before it was registered, which is precisely why
the gaps were invisible: a 1M-window model silently resolved to the 200,000
default, and a run that cost $0.078 reported $0.00. Nothing failed loudly.

Every number pinned here was read off Moonshot's own surfaces on 2026-08-05 —
``GET https://api.moonshot.ai/v1/models`` for windows and capability flags,
platform.kimi.ai/docs/pricing/chat-k3 for rates — not inferred from the
neighbouring rows.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

from src.models.capabilities import supports_vision
from src.models.configs import get_model_config
from src.models.context import (
    get_context_window_for_model,
    get_model_max_output_tokens,
)
from src.providers import get_provider_class
from src.providers.openai_compatible_specs import SPECS_BY_ID
from src.services.pricing import compute_cost, get_pricing


class TestKimiK3ModelConfig(unittest.TestCase):
    """The window is the load-bearing number — it sizes auto-compaction."""

    def test_context_window_is_the_published_1m(self) -> None:
        self.assertEqual(get_context_window_for_model("kimi-k3"), 1_048_576)

    def test_max_output_tokens(self) -> None:
        self.assertEqual(get_model_max_output_tokens("kimi-k3"), 131_072)

    def test_vision_is_asserted_not_merely_permitted(self) -> None:
        # ``capabilities.supports_vision`` trusts a verdict only from an exact
        # MODEL_CONFIGS hit, so this must come from a real row rather than the
        # unknown-model default.
        self.assertIsNotNone(get_model_config("kimi-k3"))
        self.assertTrue(supports_vision("kimi-k3"))

    def test_k2_models_keep_their_smaller_window(self) -> None:
        for model in ("kimi-k2.6", "kimi-k2.7-code", "kimi-k2.7-code-highspeed"):
            with self.subTest(model=model):
                self.assertEqual(get_context_window_for_model(model), 262_144)


class TestKimiPrefixCaptureGuard(unittest.TestCase):
    """The regression this file exists for.

    ``get_model_config`` falls back to ``key.rsplit("-", 1)[0]``, so the
    ``kimi-k3`` key claims the broad prefix ``kimi`` and captures every
    unenumerated ``kimi*`` id. Ordered with kimi-k3 first, a 262K model
    inherits a 1,048,576 window — the WIDENING direction, which overflows the
    request with a context-length 400 instead of merely compacting early.

    The k2.x rows are placed above kimi-k3 so the prefix loop reaches the
    conservative window first. This is reachable rather than hypothetical: the
    moonshot spec discovers its catalog from the endpoint, so ids nobody
    enumerated here are selectable via ``/model``.
    """

    UNENUMERATED = (
        "kimi-latest",
        "kimi-k2-turbo-preview",
        "kimi-k2.7-code-0801",
        "kimi-k3-0801",
    )

    def test_unenumerated_kimi_ids_never_inherit_the_1m_window(self) -> None:
        for model in self.UNENUMERATED:
            with self.subTest(model=model):
                self.assertEqual(
                    get_context_window_for_model(model),
                    262_144,
                    f"{model} must not inherit kimi-k3's 1M window — check that "
                    "the kimi-k2.* rows still precede kimi-k3 in MODEL_CONFIGS",
                )


class TestVendorQualifiedKimiIds(unittest.TestCase):
    """``get_model_config`` does not strip a leading ``<vendor>/``.

    So the gateways that proxy Kimi need explicit rows, exactly as
    ``openai/gpt-5.6-luna`` does. Baseten's ``moonshotai/Kimi-K3`` is shipped
    in this repo's own curated list, and before these rows it reproduced the
    bug being fixed — 200,000 against a real 1,048,576 — one ``/model``
    selection away from the id that was fixed.

    These ids carry a single hyphen, so ``moonshotai/kimi-k2``,
    ``moonshotai/kimi-k2.6`` and ``moonshotai/kimi-k3`` all reduce to the SAME
    base ``moonshotai/kimi``. The smallest window must therefore lead. Windows
    for the lowercase ids come from OpenRouter's live catalogue (2026-08-05);
    the capitalised rows are the HuggingFace-style spelling the same weights
    are served under, and share the lowercase figures.
    """

    def test_each_proxied_id_gets_its_real_window(self) -> None:
        # Exact matches only — this pins values. Ordering is the sibling
        # test's job; do not delete that one thinking this covers it.
        for model, window in (
            ("moonshotai/kimi-k2", 131_072),
            ("moonshotai/kimi-k2-0905", 262_144),
            ("moonshotai/kimi-k2-thinking", 262_144),
            ("moonshotai/kimi-k2.5", 262_144),
            ("moonshotai/kimi-k2.6", 262_144),
            ("moonshotai/kimi-k2.7-code", 262_144),
            ("moonshotai/kimi-k3", 1_048_576),
            ("moonshotai/Kimi-K3", 1_048_576),
        ):
            with self.subTest(model=model):
                self.assertEqual(get_context_window_for_model(model), window)

    def test_unenumerated_proxied_ids_fall_to_the_smallest_window(self) -> None:
        # Both namespaces. A future ``moonshotai/kimi-*`` or ``moonshotai/Kimi-*``
        # must not inherit k3's 1M — under-estimating compacts early,
        # over-estimating dies on a context-length 400.
        #
        # The capitalised cases are a REGRESSION guard, not a hypothetical:
        # registering moonshotai/Kimi-K3 without a capital-K guard moved every
        # HF-spelled id from the 200,000 default to 1,048,576.
        for model in (
            "moonshotai/kimi-unknown-future",
            "moonshotai/Kimi-K2-Instruct",
            "moonshotai/Kimi-VL-A3B-Thinking",
            "moonshotai/Kimi-Dev-72B",
            "moonshotai/Kimi-Unknown",
        ):
            with self.subTest(model=model):
                self.assertEqual(
                    get_context_window_for_model(model),
                    131_072,
                    f"{model}: the -k2/-K2 rows must remain FIRST in their "
                    "respective moonshotai/ namespaces",
                )

    def test_baseten_id_has_a_window_but_no_price(self) -> None:
        # Window and price are separate questions: Baseten sets its own rates
        # (so no pricing row) but serves the same 1M-context weights.
        self.assertEqual(get_context_window_for_model("moonshotai/Kimi-K3"), 1_048_576)
        self.assertIsNone(get_pricing("moonshotai/Kimi-K3"))

    def test_openrouter_id_is_both_windowed_and_priced(self) -> None:
        # The asymmetry this class closes: get_pricing strips the vendor
        # prefix and get_model_config does not, so registering the tier
        # without a row left this id priced at Moonshot's rate while still
        # sized to the 200,000 default.
        self.assertEqual(get_context_window_for_model("moonshotai/kimi-k3"), 1_048_576)
        self.assertIsNotNone(get_pricing("moonshotai/kimi-k3"))


class TestKimiK3PublishedRates(unittest.TestCase):
    """Pin kimi-k3's ABSOLUTE rates, following the gpt-5.6-luna post-mortem.

    That row sat at exactly half its published price for its whole registered
    life because the internal ratios were self-consistent and only an external
    number could contradict them. Source for these:
    platform.kimi.ai/docs/pricing/chat-k3 (2026-08-05).
    """

    def test_rates_match_the_vendor_pricing_page(self) -> None:
        p = get_pricing("kimi-k3")
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p["input"] * 1e6, 3.00, places=6)
        self.assertAlmostEqual(p["output"] * 1e6, 15.00, places=6)
        self.assertAlmostEqual(p["cache_read"] * 1e6, 0.30, places=6)

    def test_any_vendor_prefix_strips_to_the_same_tier(self) -> None:
        # get_pricing strips a leading ``<vendor>/`` whatever the vendor is.
        # NOT OpenRouter coverage — OpenRouter emits ``moonshotai/kimi-k3``,
        # which TestVendorQualifiedKimiIds covers for both price AND window.
        # This id is priced but unwindowed; no gateway emits it.
        self.assertEqual(get_pricing("moonshot/kimi-k3"), get_pricing("kimi-k3"))

    def test_baseten_capitalised_id_stays_unpriced(self) -> None:
        # Baseten serves its own ``moonshotai/Kimi-K3`` at its own rates. The
        # vendor-prefix strip reduces it to ``Kimi-K3``, which misses the
        # case-sensitive key — the right outcome, pinned because it currently
        # survives on capitalization alone.
        self.assertIsNone(get_pricing("moonshotai/Kimi-K3"))

    def test_k2_models_are_deliberately_unpriced(self) -> None:
        # Moonshot publishes no rates for these; guessing would be worse than
        # reporting nothing.
        for model in ("kimi-k2.6", "kimi-k2.7-code", "kimi-k2.7-code-highspeed"):
            with self.subTest(model=model):
                self.assertIsNone(get_pricing(model))

    def test_cost_of_a_real_measured_run(self) -> None:
        # Usage record from an actual 4-turn agentic run against kimi-k3.
        # Before registration this returned 0.0.
        usage = {
            "input_tokens": 19_031,
            "output_tokens": 296,
            "cache_read_input_tokens": 55_552,
            "cache_creation_input_tokens": 0,
        }
        expected = 19_031 * 3.0e-6 + 296 * 15.0e-6 + 55_552 * 0.30e-6
        self.assertAlmostEqual(compute_cost("kimi-k3", usage), expected, places=9)
        self.assertGreater(compute_cost("kimi-k3", usage), 0.0)


class TestMoonshotSpec(unittest.TestCase):
    def test_kimi_k3_is_selectable(self) -> None:
        # Asserted against the SPEC, not ``get_available_models()``: the spec
        # is now a hybrid dynamic catalog, and that call fires a live
        # ``GET /models`` whenever the discovery cache is cold — which, under
        # the per-test config dir, it always is.
        # The one fact not pinned elsewhere: kimi-k3 is in the picker at all,
        # so ``/model kimi-k3`` passes the membership check that used to
        # reject it. default_model is pinned by VENDOR_DEFAULTS in
        # test_provider_registry.py; behavioural discovery coverage is carried
        # by the parameterized guards in test_opencode_compat_providers.py,
        # which moonshot now joins.
        self.assertIn("kimi-k3", SPECS_BY_ID["moonshot"].available_models)


class TestMoonshotEffortVocabulary(unittest.TestCase):
    """Moonshot accepts low | high | max and IGNORES anything else.

    An out-of-vocabulary level does not 400 (probed: ``medium``, ``xhigh`` and
    ``bogus`` all return 200) — the field is dropped and the request lands on
    the API default, which for kimi-k3 is ``max``. So an untranslated
    ``medium`` silently buys maximum reasoning at $15/Mtok of output.
    """

    def setUp(self) -> None:
        cls = get_provider_class("moonshot")
        self.provider = cls(
            api_key="k", base_url="https://api.moonshot.ai/v1", model="kimi-k3"
        )

    def test_supported_levels_pass_through_untouched(self) -> None:
        for level in ("low", "high", "max"):
            with self.subTest(level=level):
                self.assertEqual(
                    self.provider.normalize_reasoning_effort(level), level
                )

    def test_unsupported_levels_map_to_the_nearest_supported_one(self) -> None:
        self.assertEqual(self.provider.normalize_reasoning_effort("medium"), "high")
        self.assertEqual(self.provider.normalize_reasoning_effort("xhigh"), "max")

    def test_none_stays_none(self) -> None:
        self.assertIsNone(self.provider.normalize_reasoning_effort(None))

    def test_alias_dict_is_not_shared_between_generated_classes(self) -> None:
        # ``BaseProvider.reasoning_effort_aliases`` is a mutable class-level
        # default; one shared object across generated classes would let a
        # mutation through any provider leak into every other.
        other = get_provider_class("cerebras")
        moonshot = get_provider_class("moonshot")
        self.assertIsNot(
            other.reasoning_effort_aliases, moonshot.reasoning_effort_aliases
        )
        self.assertEqual(other.reasoning_effort_aliases, {})

    def test_providers_without_a_declared_vocabulary_still_pass_through(self) -> None:
        cls = get_provider_class("cerebras")
        provider = cls(api_key="k")
        self.assertIsNone(cls.supported_reasoning_efforts)
        for level in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(level=level):
                self.assertEqual(provider.normalize_reasoning_effort(level), level)


class TestHarborAdvisorCredentials(unittest.TestCase):
    """The advisor forwards its OWN provider's key, and moonshot was missing.

    Parsed rather than imported: ``clawcodex_agent`` runs inside Harbor's venv
    and uses ``typing.override`` (3.12+), so importing it here would couple
    this suite to the interpreter version.

    Without the entry, ``_advisor_env_vars`` falls back to the union of all
    known vars — which does not contain MOONSHOT_API_KEY — and a
    ``moonshot:kimi-k3`` advisor reaches the container with no credentials.
    It then fails quietly: the worker carries on and the task can still score
    1.0, so the reward alone never reveals it.
    """

    def test_moonshot_keys_are_forwarded_to_the_container(self) -> None:
        path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "eval"
            / "harbor"
            / "clawcodex_agent.py"
        )
        tree = ast.parse(path.read_text())
        table = None
        for node in ast.walk(tree):
            target = node.target if isinstance(node, ast.AnnAssign) else None
            if isinstance(target, ast.Name) and target.id == "_PROVIDER_ENV_VARS":
                table = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(table, "_PROVIDER_ENV_VARS not found in the adapter")
        self.assertEqual(table["moonshot"], ("MOONSHOT_API_KEY", "KIMI_API_KEY"))

    def test_the_adapter_map_matches_the_provider_spec(self) -> None:
        self.assertEqual(
            SPECS_BY_ID["moonshot"].env_vars, ("MOONSHOT_API_KEY", "KIMI_API_KEY")
        )


if __name__ == "__main__":
    unittest.main()

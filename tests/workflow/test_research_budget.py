"""#283 — deep-research token budget: meta default + graceful degradation.

Covers the engine-side ``meta.default_budget`` resolution (with the
``CLAWCODEX_<NAME>_TOKEN_BUDGET`` env override) and the bundled
deep-research script's budget-aware Verify gating: when the remaining
budget can't afford the full verify fan-out, unaffordable claims pass
through unverified (logged), and Synthesize still runs.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from src.workflow.runtime import _resolve_default_budget, run_workflow
from src.workflow.sandbox import extract_meta
from src.workflow.types import AgentOutcome

_DEEP_RESEARCH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src"
    / "workflow"
    / "bundled"
    / "deep_research.py"
).read_text()


@pytest.fixture(autouse=True)
def _no_ambient_budget_env(monkeypatch):
    """A developer's exported override must not change test meaning."""
    monkeypatch.delenv("CLAWCODEX_DEEP_RESEARCH_TOKEN_BUDGET", raising=False)


def _meta(name="deep-research", **raw):
    return SimpleNamespace(name=name, raw=raw)


class TestResolveDefaultBudget:
    def test_meta_default_applies(self):
        assert _resolve_default_budget(_meta(default_budget=400000)) == 400000

    def test_no_default_returns_none(self):
        assert _resolve_default_budget(_meta()) is None

    def test_non_positive_or_bool_defaults_ignored(self):
        assert _resolve_default_budget(_meta(default_budget=0)) is None
        assert _resolve_default_budget(_meta(default_budget=-5)) is None
        assert _resolve_default_budget(_meta(default_budget=True)) is None
        assert _resolve_default_budget(_meta(default_budget="300000")) is None

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("CLAWCODEX_DEEP_RESEARCH_TOKEN_BUDGET", "123456")
        assert _resolve_default_budget(_meta(default_budget=400000)) == 123456

    def test_env_zero_disables(self, monkeypatch):
        monkeypatch.setenv("CLAWCODEX_DEEP_RESEARCH_TOKEN_BUDGET", "0")
        assert _resolve_default_budget(_meta(default_budget=400000)) is None

    def test_malformed_env_falls_back_to_meta(self, monkeypatch):
        monkeypatch.setenv("CLAWCODEX_DEEP_RESEARCH_TOKEN_BUDGET", "lots")
        assert _resolve_default_budget(_meta(default_budget=400000)) == 400000

    def test_bundled_deep_research_declares_a_default(self):
        meta = extract_meta(_DEEP_RESEARCH)
        assert _resolve_default_budget(meta) == 400000


class TestMetaDefaultWiring:
    async def test_meta_default_reaches_the_script(self, runner):
        source = (
            'meta = {"name": "t", "description": "d", "default_budget": 1234}\n'
            "return {'total': budget.total}\n"
        )
        result = await run_workflow(source, runner=runner)
        assert result.error is None
        assert result.value == {"total": 1234}

    async def test_explicit_budget_beats_meta_default(self, runner):
        source = (
            'meta = {"name": "t", "description": "d", "default_budget": 1234}\n'
            "return {'total': budget.total}\n"
        )
        result = await run_workflow(source, runner=runner, budget_total=99)
        assert result.value == {"total": 99}


def _research_handler(search_tokens: int, claim_count: int = 3):
    """A runner handler producing deterministic search/verify/synthesize
    outcomes with controllable token spend."""

    def handler(spec, index):
        schema_props = (spec.schema or {}).get("properties", {})
        if "claims" in schema_props:
            return AgentOutcome(
                structured={
                    "claims": [
                        {"claim": f"claim {index}-{i}", "source": f"https://s/{index}/{i}"}
                        for i in range(claim_count)
                    ]
                },
                tokens=search_tokens,
            )
        if "verdict" in schema_props:
            return AgentOutcome(
                structured={"verdict": "supported", "reason": "checks out"},
                tokens=1000,
            )
        return AgentOutcome(text="the report", tokens=1000)

    return handler


class TestDeepResearchDegradation:
    async def test_tight_budget_skips_verification_but_synthesizes(self, make_runner):
        # 4 searches x 4k = 16k spent of 20k; remaining 4k < the 40k
        # synthesize reserve -> zero verifiers, everything passes through.
        runner = make_runner(handler=_research_handler(search_tokens=4000, claim_count=1))
        result = await run_workflow(
            _DEEP_RESEARCH,
            runner=runner,
            args={"question": "what is up?"},
            budget_total=20000,
        )
        assert result.error is None, result.error
        assert result.value["report"] == "the report"
        # 4 distinct claims (one per angle), none dropped.
        assert result.value["claims_verified"] == result.value["claims_gathered"] == 4
        # 4 search agents + 1 synthesize; NO verify agents launched.
        assert runner.call_count == 5

    async def test_partial_budget_verifies_what_it_can_afford(self, make_runner):
        # 4 searches x 10k = 40k spent of 100k; remaining 60k - 40k
        # reserve = 20k at ~10k/verifier -> 2 of 4 claims verified, the
        # other 2 pass through unverified.
        runner = make_runner(handler=_research_handler(search_tokens=10000, claim_count=1))
        result = await run_workflow(
            _DEEP_RESEARCH,
            runner=runner,
            args={"question": "what is up?"},
            budget_total=100000,
        )
        assert result.error is None, result.error
        assert result.value["claims_verified"] == 4  # 2 verified + 2 pass-through
        # 4 search + 2 verify + 1 synthesize.
        assert runner.call_count == 7

    async def test_no_budget_verifies_everything(self, make_runner):
        runner = make_runner(handler=_research_handler(search_tokens=10000, claim_count=1))
        result = await run_workflow(
            _DEEP_RESEARCH,
            runner=runner,
            args={"question": "what is up?"},
            budget_total=None,
        )
        # The meta default (400k) applies — far above this run's spend,
        # so all 4 claims are verified.
        assert result.error is None, result.error
        assert result.value["claims_verified"] == 4
        assert runner.call_count == 9  # 4 search + 4 verify + 1 synthesize

    async def test_failed_verifier_passes_claim_through(self, make_runner):
        calls = {"verify": 0}

        def handler(spec, index):
            schema_props = (spec.schema or {}).get("properties", {})
            if "claims" in schema_props:
                return AgentOutcome(
                    structured={"claims": [{"claim": f"c{index}", "source": "https://s"}]},
                    tokens=10,
                )
            if "verdict" in schema_props:
                calls["verify"] += 1
                if calls["verify"] == 1:
                    return AgentOutcome(error="verifier crashed")
                return AgentOutcome(
                    structured={"verdict": "supported", "reason": "ok"}, tokens=10
                )
            return AgentOutcome(text="the report", tokens=10)

        runner = make_runner(handler=handler)
        result = await run_workflow(
            _DEEP_RESEARCH,
            runner=runner,
            args={"question": "q?"},
        )
        assert result.error is None, result.error
        # The crashed verifier's claim is NOT dropped.
        assert result.value["claims_verified"] == 4

    async def test_wave_overshoot_falls_back_to_raw_claims(self, make_runner):
        # The verbose-model incident profile: spend within the
        # already-launched Search wave blows past the whole budget
        # (4 x 120k > 400k). The run must still produce a report-shaped
        # result — the raw claims — instead of failing after full spend.
        runner = make_runner(handler=_research_handler(search_tokens=120000, claim_count=1))
        result = await run_workflow(
            _DEEP_RESEARCH,
            runner=runner,
            args={"question": "what is up?"},
        )
        assert result.error is None, result.error
        assert "raw cross-checked claims" in result.value["report"]
        assert "claim" in result.value["report"]  # the bullets made it in
        assert result.value["claims_verified"] == 4  # all pass through
        # 4 search agents only — no verify, no synthesize agent.
        assert runner.call_count == 4

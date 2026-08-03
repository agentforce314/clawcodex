"""Advisor wiring in the harbor eval adapter.

SKIPPED IN CI, for the same reason as ``test_harbor_adapter_fusion``:
``eval/harbor/clawcodex_agent.py`` imports ``harbor`` at module scope and
harbor is an eval-only uv tool, not a dev dependency. These assertions run
for the people who run evals and nowhere else — do not read a green CI as
coverage of this file.

The failure mode being pinned here is a QUIET one. A consultation that
cannot authenticate degrades gracefully: the worker carries on and the task
can still score 1.0, so a run whose advisor never once answered looks
identical to a healthy one in the results table. It was found by grepping a
container trajectory, not by a red test — hence these.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("harbor", reason="harbor is an eval-only tool dependency")

_ADAPTER_DIR = Path(__file__).resolve().parents[1] / "eval" / "harbor"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from clawcodex_agent import Clawcodex  # noqa: E402


def _agent(
    *,
    model_provider: str = "openai",
    advisor: str | None = None,
    advisor_effort: str | None = None,
    subscription: bool = False,
    effort: str | None = "xhigh",
) -> Clawcodex:
    """An adapter instance carrying only the fields these paths read."""
    agent = Clawcodex.__new__(Clawcodex)
    agent._subscription = subscription
    agent._advisor = advisor
    agent._advisor_effort = advisor_effort
    agent._parsed_model_provider = model_provider
    agent._forward_keys = False
    agent._fusion = None
    agent._resolved_flags = {"effort": effort} if effort else {}
    agent._extra_env = {}
    agent._get_env = lambda key: f"{key}-VALUE"
    return agent


def _seeded_config(agent: Clawcodex) -> dict:
    captured: dict = {}

    async def fake_exec(environment, command=None, env=None):
        captured.update(json.loads(env["CLAWCODEX_SEED_CONFIG"]))

    agent.exec_as_agent = fake_exec
    agent._host_env_keys = lambda: {}
    agent._fusion_record = lambda: None
    asyncio.run(agent._seed_container_settings(None))
    return captured


# --------------------------------------------------------------------------
# Credential forwarding
# --------------------------------------------------------------------------

def test_advisor_provider_key_is_forwarded() -> None:
    """The advisor calls its OWN provider. Forwarding only the main model's
    key left it with no credentials — observed live as two consultations
    dying on "Missing credentials" while the task still scored 1.0."""
    env = _agent(model_provider="openai", advisor="zai:glm-5.2")._build_env()
    assert "OPENAI_API_KEY" in env
    assert "ZAI_API_KEY" in env


def test_anthropic_key_withheld_when_advisor_uses_the_subscription() -> None:
    """OAuth must stay the only route to the subscription: inside clawcodex
    an API key outranks it and would silently bill the API instead."""
    env = _agent(
        model_provider="openai",
        advisor="anthropic:claude-opus-5",
        subscription=True,
    )._build_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" in env, "the worker still needs its own key"


def test_main_provider_key_survives_subscription_mode() -> None:
    """Subscription used to suppress ALL provider keys, which is right only
    when the subscription backs the MAIN loop. Backing just the advisor left
    the worker with no credentials at all."""
    env = _agent(
        model_provider="openai",
        advisor="anthropic:claude-opus-5",
        subscription=True,
    )._build_env()
    assert env.get("OPENAI_API_KEY")


def test_unmapped_main_provider_still_withholds_the_anthropic_key() -> None:
    """The expensive branch.

    For a MAPPED provider the strip is a no-op (``openai`` maps to only
    OPENAI_API_KEY), so it looks redundant. It bites when the main provider
    is unmapped and falls back to _ALL_PROVIDER_ENV_VARS — which is most of
    the registry (groq, fireworks, cerebras, together, …). Without the
    strip, ANTHROPIC_API_KEY rides into the container, where an API key
    silently OUTRANKS OAuth inside clawcodex and bills the API instead of
    the subscription. That is the exact outcome subscription mode exists to
    prevent, and no mapped-provider test can catch it.
    """
    env = _agent(
        model_provider="groq",
        advisor="anthropic:claude-opus-5",
        subscription=True,
    )._build_env()
    assert "ANTHROPIC_API_KEY" not in env
    # The fallback DID fire — other mapped keys came through, so the absence
    # above is the strip doing its job, not an empty forward set.
    assert env.get("OPENAI_API_KEY"), "expected the all-providers fallback"


def test_unmapped_provider_key_is_not_forwarded_at_all() -> None:
    """PRE-EXISTING limitation, pinned so it is not mistaken for advisor
    breakage. `_ALL_PROVIDER_ENV_VARS` is the union of the seven MAPPED
    vendors, so an unmapped provider's own key (GROQ_API_KEY, TOGETHER_API_KEY,
    …) is never forwarded — the fallback is 'every key we know about', not
    'every key that exists'. A run using one of those as the worker needs its
    key passed explicitly via --ae, whether or not an advisor is configured.
    """
    env = _agent(model_provider="groq", advisor=None)._build_env()
    assert "GROQ_API_KEY" not in env


def test_anthropic_main_loop_subscription_forwards_nothing() -> None:
    """The original behaviour, unchanged."""
    env = _agent(model_provider="anthropic", subscription=True)._build_env()
    assert not [k for k in env if k.endswith("_API_KEY")]


def test_no_advisor_leaves_forwarding_untouched() -> None:
    env = _agent(model_provider="openai", advisor=None)._build_env()
    assert sorted(k for k in env if k.endswith("_API_KEY")) == ["OPENAI_API_KEY"]


# --------------------------------------------------------------------------
# Settings seeding
# --------------------------------------------------------------------------

def test_advisor_settings_are_seeded() -> None:
    config = _seeded_config(
        _agent(advisor="anthropic:claude-opus-5", advisor_effort="xhigh")
    )
    settings = config["settings"]
    assert settings["advisor_enabled"] is True, "master switch defaults off"
    assert settings["advisor_provider"] == "anthropic"
    assert settings["advisor_model"] == "claude-opus-5"
    assert settings["advisor_effort"] == "xhigh"
    # Client-side pinned so the advisor behaves identically regardless of
    # which worker model a run is comparing.
    assert settings["advisor_client_mode"] is True


def test_worker_and_advisor_efforts_are_independent() -> None:
    settings = _seeded_config(
        _agent(effort="low", advisor="anthropic:claude-opus-5", advisor_effort="max")
    )["settings"]
    assert settings["effort"] == "low"
    assert settings["advisor_effort"] == "max"


def test_advisor_effort_omitted_means_inherit() -> None:
    settings = _seeded_config(
        _agent(effort="xhigh", advisor="anthropic:claude-opus-5")
    )["settings"]
    assert "advisor_effort" not in settings


def test_no_advisor_seeds_no_advisor_keys() -> None:
    settings = _seeded_config(_agent(advisor=None))["settings"]
    assert not [k for k in settings if k.startswith("advisor")]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _construct(**kwargs):
    """Run the adapter's own __init__ validation without harbor's base
    machinery — the base __init__ needs a real environment we don't have."""
    from unittest.mock import patch

    base = Clawcodex.__mro__[1]
    with patch.object(base, "__init__", lambda self, *a, **k: None):
        agent = Clawcodex(Path("/tmp"), **kwargs)
    return agent


@pytest.mark.parametrize("bad", ["anthropic", "claude-opus-5"])
def test_advisor_without_a_colon_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="<provider>:<model>"):
        _construct(advisor=bad)


def test_valid_advisor_is_accepted_and_parsed() -> None:
    agent = _construct(advisor="anthropic:claude-opus-5")
    assert agent._advisor_provider() == "anthropic"


def test_model_may_contain_further_colons_and_slashes() -> None:
    """Only the FIRST colon separates; model ids keep their own punctuation."""
    agent = _construct(advisor="openrouter:anthropic/claude-opus-4.1")
    assert agent._advisor_provider() == "openrouter"
    assert agent._advisor.split(":", 1)[1] == "anthropic/claude-opus-4.1"


@pytest.mark.parametrize("bad", ["bogus", "XHIGH ", "10"])
def test_invalid_advisor_effort_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="advisor_effort"):
        _construct(advisor="anthropic:claude-opus-5", advisor_effort=bad)


def test_advisor_effort_without_an_advisor_is_rejected() -> None:
    """An effort level with no reviewer configured is silently inert —
    exactly the class of quiet no-op this adapter has been bitten by."""
    with pytest.raises(ValueError, match="requires 'advisor'"):
        _construct(advisor_effort="xhigh")


def test_subscription_requires_anthropic_in_some_role() -> None:
    """Rejected when nothing anthropic is present..."""
    agent = _agent(model_provider="openai", advisor=None, subscription=True)
    with pytest.raises(RuntimeError, match="requires anthropic in some role"):
        asyncio.run(agent._inject_subscription_credentials(None))


def test_subscription_accepted_for_an_anthropic_advisor() -> None:
    """...and accepted when anthropic is the ADVISOR rather than the main
    model — the pairing that was previously inexpressible. Getting past the
    role gate is the assertion; it then fails on host credentials, which is
    the next step and not this test's concern."""
    agent = _agent(
        model_provider="openai",
        advisor="anthropic:claude-opus-5",
        subscription=True,
    )
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(agent._inject_subscription_credentials(None))
    assert "requires anthropic in some role" not in str(excinfo.value)

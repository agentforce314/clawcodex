"""Vision-tool wiring in the harbor eval adapter.

Runs ONLY in the dedicated "Harbor adapter (3.13)" CI job, which installs
harbor explicitly. ``eval/harbor/clawcodex_agent.py`` imports ``harbor`` at
module scope, so under the main ``test (3.11)`` job the ``importorskip``
below fires and every assertion here skips silently. A file left out of that
job's file list therefore never runs at all — add new ``tests/test_harbor_*``
files to it.

The failure mode pinned here is the QUIET one this adapter keeps producing:
a ``vision_analyze`` whose provider key never reached the container is
advertised to the model, called, and dies on missing credentials — while the
worker carries on and the task can still score 1.0. The moonshot advisor gap
had exactly this shape and was found by grepping a container trajectory, not
by a red test.
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
    model_provider: str = "deepseek",
    vision: str | None = None,
    advisor: str | None = None,
    subscription: bool = False,
) -> Clawcodex:
    agent = Clawcodex.__new__(Clawcodex)
    agent._subscription = subscription
    agent._advisor = advisor
    agent._advisor_effort = None
    agent._vision = vision
    agent._parsed_model_provider = model_provider
    agent._forward_keys = False
    agent._fusion = None
    agent._resolved_flags = {"effort": "max"}
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


# --------------------------------------------------------------------- config


def test_vision_block_is_seeded_at_the_top_level_not_in_settings() -> None:
    """``vision_config`` reads the GLOBAL tier only, deliberately — the key
    names the provider that receives image bytes. A ``settings`` key would
    also merge from project/local tiers, which is the exposure that design
    exists to avoid."""
    cfg = _seeded_config(_agent(vision="openai:gpt-5.6-luna"))
    assert cfg["vision"] == {
        "enabled": True,
        "provider": "openai",
        "model": "gpt-5.6-luna",
    }
    assert "vision" not in cfg.get("settings", {})


def test_no_vision_block_when_unset() -> None:
    assert "vision" not in _seeded_config(_agent())


def test_vision_and_advisor_coexist() -> None:
    cfg = _seeded_config(_agent(vision="openai:gpt-5.6-luna", advisor="moonshot:kimi-k3"))
    assert cfg["vision"]["model"] == "gpt-5.6-luna"
    assert cfg["settings"]["advisor_model"] == "kimi-k3"
    assert cfg["settings"]["advisor_enabled"] is True


# ---------------------------------------------------------------- credentials


def test_vision_provider_key_is_forwarded() -> None:
    """The tool calls its OWN provider. Without this the model is offered a
    tool that cannot authenticate — and Read's stub starts naming it, so the
    failure reads like a tool bug rather than a missing key."""
    env = _agent(model_provider="deepseek", vision="openai:gpt-5.6-luna")._build_env()
    assert "DEEPSEEK_API_KEY" in env, "the worker still needs its own key"
    assert "OPENAI_API_KEY" in env


def test_worker_vision_and_advisor_keys_are_all_forwarded() -> None:
    """The three-vendor configuration this was built for."""
    env = _agent(
        model_provider="deepseek",
        vision="openai:gpt-5.6-luna",
        advisor="moonshot:kimi-k3",
    )._build_env()
    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "MOONSHOT_API_KEY"):
        assert key in env, key


def test_anthropic_key_withheld_when_vision_is_anthropic_under_subscription() -> None:
    """OAuth must stay the only route to the subscription: inside clawcodex
    an API key outranks it and would silently bill the API instead."""
    env = _agent(
        model_provider="deepseek",
        vision="anthropic:claude-opus-5",
        subscription=True,
    )._build_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "DEEPSEEK_API_KEY" in env


# ----------------------------------------------------------------- validation


@pytest.mark.parametrize("bad", ["openai:", ":gpt-5.6-luna", "openai", "  :  "])
def test_half_configured_vision_is_rejected_at_construction(bad: str) -> None:
    """Fail loudly at setup rather than seeding a silently-inert config —
    the failure mode this adapter keeps producing.

    Drives the REAL constructor, not a mirror of its check: a mirrored
    assertion passes even if the adapter drops the validation entirely.
    """
    import tempfile

    with pytest.raises(ValueError, match="vision"):
        Clawcodex(logs_dir=Path(tempfile.mkdtemp()), vision=bad)


def test_well_formed_vision_is_accepted() -> None:
    import tempfile

    agent = Clawcodex(
        logs_dir=Path(tempfile.mkdtemp()), vision="openai:gpt-5.6-luna"
    )
    assert agent._vision == "openai:gpt-5.6-luna"

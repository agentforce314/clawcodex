"""Fusion-model wiring in the harbor eval adapter.

Runs ONLY in the dedicated "Harbor adapter (3.13)" CI job, which installs
harbor explicitly. ``eval/harbor/clawcodex_agent.py`` imports ``harbor`` at
module scope, so under the main ``test (3.11)`` job the ``importorskip``
below fires and every assertion here skips silently. A file left out of that
job's file list therefore never runs at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("harbor", reason="harbor is an eval-only tool dependency")

_ADAPTER_DIR = Path(__file__).resolve().parents[1] / "eval" / "harbor"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from clawcodex_agent import Clawcodex  # noqa: E402


def _agent(fusion: str | None, model: str = "deepseek-v4-flash-luna") -> Clawcodex:
    """An adapter instance with just the fields the record builder reads."""
    agent = Clawcodex.__new__(Clawcodex)
    agent._fusion = fusion
    agent._parsed_model_name = model
    return agent


def test_a_valid_pairing_becomes_a_seedable_record() -> None:
    record = _agent("deepseek:deepseek-v4-flash+openai:gpt-5.6-luna")._fusion_record()
    assert record == {
        "name": "deepseek-v4-flash-luna",
        "base": "deepseek:deepseek-v4-flash",
        "vision": "openai:gpt-5.6-luna",
    }


def test_the_record_is_what_clawcodex_can_actually_load() -> None:
    """The seeded JSON must satisfy clawcodex's own parser.

    The adapter writes this straight into the container's global config; if
    the shape drifts, the container silently resolves no fusion model and
    falls back to the default provider — which is how this first failed.
    """
    from src.providers.fusion_models import fusion_model_from_json

    record = _agent("deepseek:deepseek-v4-flash+openai:gpt-5.6-luna")._fusion_record()
    model = fusion_model_from_json(record)
    assert model is not None
    assert model.base.selector == "deepseek:deepseek-v4-flash"
    assert model.vision.selector == "openai:gpt-5.6-luna"


def test_no_fusion_kwarg_seeds_nothing() -> None:
    assert _agent(None)._fusion_record() is None
    assert _agent("")._fusion_record() is None


def test_the_record_is_named_after_the_model_flag() -> None:
    """`--model <name>` IS the selector clawcodex looks up, so they must match."""
    record = _agent(
        "deepseek:deepseek-v4-flash+openai:gpt-5.6-luna", model="my-fusion"
    )._fusion_record()
    assert record["name"] == "my-fusion"


@pytest.mark.parametrize(
    "bad",
    [
        "deepseek:deepseek-v4-flash",  # no +vision
        "+openai:gpt-5.6-luna",  # no base
        "deepseek:deepseek-v4-flash+",  # no vision
        "deepseek-v4-flash+openai:gpt-5.6-luna",  # base missing provider:
        "deepseek:deepseek-v4-flash+gpt-5.6-luna",  # vision missing provider:
    ],
)
def test_a_malformed_pairing_fails_loudly(bad: str) -> None:
    """Loudly, because the alternative is a silent fallback.

    An undeclared or unparsed value here does not error at the container —
    it just leaves no fusion record, and the run proceeds against the
    default provider looking superficially fine.
    """
    with pytest.raises(ValueError):
        _agent(bad)._fusion_record()


def test_the_two_malformed_shapes_report_different_causes() -> None:
    """A wrong SHAPE and a wrong SELECTOR need different messages.

    Both are rejected either way — the `":" in part` check alone catches
    every malformed case — so the shape check earns its place purely by
    telling the operator which mistake they made. Without this assertion it
    is an equivalent mutant and reads as dead code.
    """
    with pytest.raises(ValueError, match=r"<base>\+<vision>"):
        _agent("deepseek:deepseek-v4-flash")._fusion_record()

    with pytest.raises(ValueError, match=r"must be <provider>:<model>"):
        _agent("deepseek-v4-flash+openai:gpt-5.6-luna")._fusion_record()


def test_fusion_without_a_model_flag_fails_loudly() -> None:
    with pytest.raises(ValueError, match="requires --model"):
        _agent("deepseek:deepseek-v4-flash+openai:gpt-5.6-luna", model="")._fusion_record()


def test_fusion_and_forward_keys_are_declared_constructor_kwargs() -> None:
    """Not CLI_FLAGS entries — that distinction is load-bearing.

    `_resolve_flag_values` only walks `CLI_FLAGS`, so an undeclared `--ak`
    key is accepted and discarded without complaint. `forward_keys` was read
    from `_resolved_flags` while declared nowhere, which made the documented
    `--ak forward_keys=false` opt-out a no-op. Declaring them in `CLI_FLAGS`
    instead is not the fix: `build_cli_flags()` emits every entry, so
    clawcodex would be passed a `--fusion` flag it rejects.
    """
    import inspect

    params = inspect.signature(Clawcodex.__init__).parameters
    assert "fusion" in params
    assert "forward_keys" in params
    assert not any(
        flag.kwarg in {"fusion", "forward_keys"} for flag in Clawcodex.CLI_FLAGS
    )

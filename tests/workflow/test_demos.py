"""Integration guard: the shipped Python demo workflows are valid against the
engine.

Every file in ``demos/workflows/*.py`` must (1) define an extractable ``meta``
block and (2) compile as a workflow body (i.e. parse + wrap in the
``async def __workflow_main__`` envelope). This catches regressions in the demo
corpus and confirms the converted scripts conform to the sandbox's expectations
without needing a live model.
"""

from __future__ import annotations

import pathlib

import pytest

from src.workflow.runtime import run_workflow
from src.workflow.sandbox import compile_workflow, extract_meta

# Exception types that indicate a *port/API* bug (vs. a demo's own input guard).
_API_MISUSE_PREFIXES = ("TypeError", "NameError", "AttributeError", "SyntaxError", "WorkflowError")

# Generous args superset so each demo finds the keys it needs and proceeds past
# its input guards into actually spawning agents.
_DEMO_ARGS = {
    "keyword": "wireless earbuds",
    "productKeyword": "wireless earbuds",
    "topic": "wireless earbuds",
    "brand": "Acme",
    "productUrl": "https://example.com/product",
    "product_url": "https://example.com/product",
    "title": "Why Wireless Earbuds Beat Wired",
    "count": 3,
    "maxQuestions": 3,
    "question": "Which wireless earbuds are best for running?",
}

_DEMO_DIR = pathlib.Path(__file__).resolve().parents[2] / "demos" / "workflows"
_DEMO_FILES = sorted(_DEMO_DIR.glob("*.py"))


def test_demo_corpus_present():
    # The four converted demos (02-05) should exist.
    names = {p.name for p in _DEMO_FILES}
    assert _DEMO_FILES, f"no Python demos found in {_DEMO_DIR}"
    assert any(n.startswith("02-") for n in names)
    assert any(n.startswith("05-") for n in names)


@pytest.mark.parametrize("path", _DEMO_FILES, ids=lambda p: p.name)
def test_demo_has_valid_meta(path: pathlib.Path):
    meta = extract_meta(path.read_text())
    assert meta.name
    assert meta.description
    # phases, when used, are a list of dicts with titles
    for phase in meta.phases:
        assert isinstance(phase, dict)
        assert phase.get("title")


@pytest.mark.parametrize("path", _DEMO_FILES, ids=lambda p: p.name)
def test_demo_compiles_as_workflow(path: pathlib.Path):
    # Raises on syntax/compile error; the wrapper makes top-level await/return valid.
    compile_workflow(path.read_text())


@pytest.mark.parametrize("path", _DEMO_FILES, ids=lambda p: p.name)
async def test_demo_runs_against_the_engine(path: pathlib.Path, schema_runner):
    # Actually execute each demo through the real engine with a schema-aware
    # fake. A genuine API-misuse / port bug (positional opts, undefined name,
    # bad await) surfaces as a TypeError/NameError; a demo's own input guard
    # (RuntimeError/ValueError) is acceptable. This is what compile-only can't catch.
    runner = schema_runner
    result = await run_workflow(path.read_text(), runner=runner, args=dict(_DEMO_ARGS), max_concurrent=4)
    if result.error is not None:
        prefix = result.error.split(":", 1)[0]
        assert prefix not in _API_MISUSE_PREFIXES, f"{path.name} hit a port/API bug: {result.error}"
    # The engine must have driven the script into execution: either it spawned
    # agents, or it raised its own input guard — never a silent no-op.
    assert runner.calls or result.error is not None, f"{path.name} silently did nothing"

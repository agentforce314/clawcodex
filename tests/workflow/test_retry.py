"""Tests for the `r` (retry) action — re-spawning a running agent (#3)."""

from __future__ import annotations

from src.workflow.constants import MAX_AGENT_RETRIES
from src.workflow.runtime import run_workflow
from src.workflow.types import AgentOutcome

META = 'meta = {"name": "t", "description": "d"}\n'


async def test_agent_retry_reruns_and_recovers(make_runner):
    state = {"n": 0, "run": None}

    def handler(spec, index):
        state["n"] += 1
        if state["n"] == 1:
            state["run"].retry_agent(index)  # ask to retry this very agent
            return AgentOutcome(skipped=True)
        return AgentOutcome(text="recovered")

    runner = make_runner(handler=handler)
    res = await run_workflow(
        META + 'return await agent("x")',
        runner=runner,
        on_start=lambda run: state.__setitem__("run", run),
    )
    assert res.ok
    assert res.value == "recovered"
    assert state["n"] == 2  # initial attempt + one retry


async def test_agent_retry_is_bounded(make_runner):
    state = {"n": 0, "run": None}

    def handler(spec, index):
        state["n"] += 1
        state["run"].retry_agent(index)  # always ask to retry
        return AgentOutcome(skipped=True)

    runner = make_runner(handler=handler)
    res = await run_workflow(
        META + 'return await agent("x")',
        runner=runner,
        on_start=lambda run: state.__setitem__("run", run),
    )
    assert res.ok
    assert res.value is None  # retries exhausted -> skipped -> None
    assert state["n"] == MAX_AGENT_RETRIES + 1


async def test_skip_resolves_to_none_without_aborting_the_run(make_runner):
    # A single-agent abort (skip) must resolve that agent to None and let the
    # rest of the script continue — it must NOT propagate and end the run.
    state = {"run": None}

    def handler(spec, index):
        if spec.prompt == "skipme":
            state["run"].abort_agent(index)  # skip this one
            return AgentOutcome(skipped=True)
        return AgentOutcome(text=spec.prompt)

    runner = make_runner(handler=handler)
    script = META + (
        'a = await agent("first")\n'
        'b = await agent("skipme")\n'
        'c = await agent("third")\n'
        "return [a, b, c]\n"
    )
    res = await run_workflow(script, runner=runner, on_start=lambda run: state.__setitem__("run", run))
    assert res.ok
    assert res.value == ["first", None, "third"]

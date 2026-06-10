"""End-to-end tests for the workflow runtime, driven by the FakeRunner."""

from __future__ import annotations

import src.workflow.runtime as runtime_mod
import src.workflow.scheduler as scheduler_mod
from src.workflow.runtime import run_workflow
from src.workflow.types import AgentOutcome

META = 'meta = {"name": "t", "description": "d"}\n'


# ── basics ───────────────────────────────────────────────────────────────────


async def test_basic_run_returns_value(runner):
    res = await run_workflow(META + 'return await agent("hi")', runner=runner)
    assert res.ok
    assert res.value == "r0"
    assert runner.call_count == 1


async def test_meta_is_parsed_and_exposed(runner):
    res = await run_workflow(
        'meta = {"name": "named", "description": "d", "phases": [{"title": "A"}]}\nreturn 1',
        runner=runner,
    )
    assert res.meta.name == "named"
    assert res.meta.phases == [{"title": "A"}]


async def test_bad_meta_raises_preflight(runner):
    import pytest

    from src.workflow.errors import WorkflowMetaError

    with pytest.raises(WorkflowMetaError):
        await run_workflow("return 1", runner=runner)


async def test_script_runtime_error_is_captured(runner):
    res = await run_workflow(META + 'raise ValueError("nope")', runner=runner)
    assert not res.ok
    assert "ValueError" in res.error
    assert "nope" in res.error


# ── agent: text vs schema, failure -> None ───────────────────────────────────


async def test_schema_agent_returns_structured(runner):
    res = await run_workflow(
        META + 'return await agent("x", schema={"type": "object"})', runner=runner
    )
    assert res.value == {"echo": "x"}


async def test_failed_subagent_resolves_to_none(make_runner):
    def handler(spec, index):
        if "boom" in spec.prompt:
            raise RuntimeError("subagent died")
        return AgentOutcome(text=f"ok{index}")

    runner = make_runner(handler=handler)
    res = await run_workflow(META + 'return await agent("boom")', runner=runner)
    assert res.ok  # the run does not crash...
    assert res.value is None  # ...the agent just resolves to None


async def test_error_outcome_resolves_to_none(make_runner):
    runner = make_runner(handler=lambda s, i: AgentOutcome(error="terminal"))
    res = await run_workflow(META + 'return await agent("x")', runner=runner)
    assert res.value is None


# ── parallel ─────────────────────────────────────────────────────────────────


async def test_parallel_preserves_order_and_nulls_failures(make_runner):
    def handler(spec, index):
        if spec.prompt == "boom":
            raise RuntimeError("x")
        return AgentOutcome(text=spec.prompt.upper())

    runner = make_runner(handler=handler)
    script = META + 'return await parallel([agent("a"), agent("boom"), agent("c")])'
    res = await run_workflow(script, runner=runner)
    assert res.value == ["A", None, "C"]


async def test_parallel_respects_concurrency_cap(make_runner):
    runner = make_runner(delay=0.02)
    script = META + "return await parallel([agent(str(i)) for i in range(12)])"
    res = await run_workflow(script, runner=runner, max_concurrent=3)
    assert len(res.value) == 12
    assert runner.peak <= 3


# ── pipeline ─────────────────────────────────────────────────────────────────


async def test_pipeline_runs_stages_per_item(runner):
    script = META + (
        "return await pipeline([1, 2, 3],\n"
        "    lambda prev, item, index: prev + 1,\n"
        "    lambda prev, item, index: prev * 10)\n"
    )
    res = await run_workflow(script, runner=runner)
    assert res.value == [20, 30, 40]


async def test_pipeline_drops_failing_item(runner):
    script = META + (
        "def stage(prev, item, index):\n"
        "    if item == 2:\n"
        "        raise ValueError('bad')\n"
        "    return prev * 100\n"
        "return await pipeline([1, 2, 3], stage)\n"
    )
    res = await run_workflow(script, runner=runner)
    assert res.value == [100, None, 300]


# ── caps & budget ────────────────────────────────────────────────────────────


async def test_budget_ceiling_stops_the_run(runner):
    # Each agent costs 5 tokens; total 8 -> the third call trips the ceiling.
    script = META + (
        'a = await agent("1")\n'
        'b = await agent("2")\n'
        'c = await agent("3")\n'
        "return [a, b, c]\n"
    )
    res = await run_workflow(script, runner=runner, budget_total=8)
    assert not res.ok
    assert "budget" in res.error.lower()
    assert runner.call_count == 2  # third never reached the runner


async def test_per_call_item_cap(monkeypatch, runner):
    monkeypatch.setattr(runtime_mod, "MAX_ITEMS_PER_CALL", 3)
    script = META + "return await parallel([agent(str(i)) for i in range(4)])"
    res = await run_workflow(script, runner=runner)
    assert not res.ok
    assert "per-call cap" in res.error


async def test_per_run_agent_cap(monkeypatch, runner):
    monkeypatch.setattr(scheduler_mod, "MAX_AGENTS_PER_RUN", 2)
    script = META + (
        'await agent("1")\n'
        'await agent("2")\n'
        'await agent("3")\n'
        "return 'done'\n"
    )
    res = await run_workflow(script, runner=runner)
    assert not res.ok
    assert "per-run agent cap" in res.error


# ── abort ────────────────────────────────────────────────────────────────────


async def test_pre_aborted_run_stops(runner):
    from src.utils.abort_controller import create_abort_controller

    controller = create_abort_controller()
    controller.abort("user")
    res = await run_workflow(META + 'return await agent("x")', runner=runner, controller=controller)
    assert not res.ok
    assert "abort" in res.error.lower()
    assert runner.call_count == 0  # never spawned


# ── resume ───────────────────────────────────────────────────────────────────


async def test_resume_replays_cached_results(make_runner):
    script = META + (
        'a = await agent("one")\n'
        'b = await agent("two")\n'
        "return [a, b]\n"
    )
    first = make_runner()
    run1 = await run_workflow(script, runner=first)
    assert first.call_count == 2

    second = make_runner()
    run2 = await run_workflow(script, runner=second, resume=run1.journal)
    assert run2.value == run1.value
    assert second.call_count == 0  # everything served from the journal


async def test_resume_keys_are_deterministic_under_multiround_fanout(make_runner):
    # Two concurrent branches, each doing a SECOND-round agent that depends on
    # its first-round result. Regression for the spawn-order keying bug: the
    # call-path key for "branch X's round-2 agent" must be the same no matter
    # which branch's round-1 finished first.
    import asyncio

    script = META + (
        "async def branch(tag):\n"
        "    r1 = await agent('r1-' + tag)\n"
        "    r2 = await agent('r2-' + tag + '-' + str(r1))\n"
        "    return r2\n"
        "return await parallel([branch('A'), branch('B')])\n"
    )

    def slow(tag):
        async def handler(spec, index):
            if spec.prompt.startswith("r1-") and tag in spec.prompt:
                await asyncio.sleep(0.02)  # make this branch's round 1 finish last
            return AgentOutcome(text="ok", tokens=1)

        return handler

    run_a = await run_workflow(script, runner=make_runner(handler=slow("A")))
    run_b = await run_workflow(script, runner=make_runner(handler=slow("B")))

    # Each call-path key must map to the SAME logical call (same fingerprint)
    # regardless of which branch was slow — the heart of the fix.
    fp_a = {k: r.fingerprint for k, r in run_a.journal.items()}
    fp_b = {k: r.fingerprint for k, r in run_b.journal.items()}
    assert fp_a == fp_b

    # And resuming across the two timings is a full cache hit.
    third = make_runner(handler=slow("A"))
    run_c = await run_workflow(script, runner=third, resume=run_b.journal)
    assert third.call_count == 0


async def test_resume_reruns_after_divergence(make_runner):
    base = META + 'a = await agent("one")\nb = await agent("SECOND")\nreturn [a, b]\n'
    first = make_runner()
    run1 = await run_workflow(base.replace("SECOND", "two"), runner=first)

    second = make_runner()
    run2 = await run_workflow(base.replace("SECOND", "CHANGED"), runner=second, resume=run1.journal)
    # index 0 cached, index 1 changed -> exactly one live call.
    assert second.call_count == 1
    assert run2.value[0] == run1.value[0]  # prefix preserved


# ── nested workflow ──────────────────────────────────────────────────────────


async def test_nested_workflow(runner):
    sub = 'meta = {"name": "sub", "description": "d"}\nreturn await agent("inner:" + args["q"])'

    def resolve(name):
        assert name == "sub"
        return sub

    script = META + 'return await workflow("sub", args={"q": "hello"})'
    res = await run_workflow(script, runner=runner, resolve_workflow=resolve)
    assert res.ok
    # The nested run's agent sits under the parent's consumed slot -> key "0.0".
    assert res.value == "r0.0"
    assert runner.calls[0].prompt == "inner:hello"


async def test_nested_workflow_depth_guard(runner):
    inner = 'meta = {"name": "i", "description": "d"}\nreturn await workflow("x")'

    def resolve(name):
        return inner

    script = META + 'return await workflow("i")'
    res = await run_workflow(script, runner=runner, resolve_workflow=resolve)
    assert not res.ok
    assert "nesting is one level" in res.error


# ── progress ─────────────────────────────────────────────────────────────────


async def test_progress_tracks_phases_agents_and_statuses(make_runner):
    def handler(spec, index):
        if spec.prompt == "fail":
            return AgentOutcome(error="x")
        return AgentOutcome(text="ok", tokens=7)

    runner = make_runner(handler=handler)
    script = (
        'meta = {"name": "p", "description": "d", "phases": [{"title": "One"}]}\n'
        'phase("One")\n'
        'log("hello")\n'
        'await agent("ok", label="good")\n'
        'await agent("fail", label="bad")\n'
        "return 1\n"
    )
    res = await run_workflow(script, runner=runner)
    prog = res.progress
    assert prog.agent_count == 2
    assert prog.token_total == 7
    assert "hello" in prog.logs
    statuses = {a.label: a.status for p in prog.phases for a in p.agents}
    assert statuses["good"] == "completed"
    assert statuses["bad"] == "failed"
    assert any(p.title == "One" for p in prog.phases)

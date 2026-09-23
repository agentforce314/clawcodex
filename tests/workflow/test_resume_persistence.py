"""Tests for journal disk-persistence + same-session resume (Phase 9)."""

from __future__ import annotations

from pathlib import Path

from src.task_registry import RuntimeTaskRegistry
from src.workflow.launch import load_journal, run_workflow_task

META = 'meta = {"name": "t", "description": "d"}\n'
SCRIPT = META + 'a = await agent("one")\nb = await agent("two")\nreturn [a, b]\n'


async def test_journal_is_persisted(make_runner, tmp_path):
    out = str(tmp_path / "wf" / "run1.json")
    reg = RuntimeTaskRegistry()
    runner = make_runner()
    await run_workflow_task(
        source=SCRIPT, runner=runner, registry=reg, task_id="w1", run_id="run1", output_file=out
    )
    assert Path(out).is_file()
    assert runner.call_count == 2
    journal = load_journal(out)
    assert journal is not None and len(journal) == 2


async def test_resume_from_persisted_journal_is_full_cache(make_runner, tmp_path):
    out1 = str(tmp_path / "wf" / "run1.json")
    reg1 = RuntimeTaskRegistry()
    first = make_runner()
    res1 = await run_workflow_task(
        source=SCRIPT, runner=first, registry=reg1, task_id="w1", run_id="run1", output_file=out1
    )

    # Fresh run, resuming from the persisted journal -> nothing re-runs live.
    reg2 = RuntimeTaskRegistry()
    second = make_runner()
    res2 = await run_workflow_task(
        source=SCRIPT,
        runner=second,
        registry=reg2,
        task_id="w2",
        run_id="run2",
        output_file=str(tmp_path / "wf" / "run2.json"),
        resume=load_journal(out1),
    )
    assert second.call_count == 0
    assert res2.value == res1.value
    assert reg2.get("w2").status == "completed"


def test_load_journal_missing_file_is_none(tmp_path):
    assert load_journal(str(tmp_path / "nope.json")) is None


async def test_completed_branch_is_on_disk_before_other_work_finishes(
    make_runner, tmp_path
):
    from src.workflow.types import AgentOutcome

    output_file = str(tmp_path / "checkpoint.json")

    def handler(spec, index):
        if spec.prompt == "two":
            checkpoint = load_journal(output_file)
            assert checkpoint is not None and len(checkpoint) == 1
        return AgentOutcome(text=spec.prompt, tokens=5)

    result = await run_workflow_task(
        source=SCRIPT,
        runner=make_runner(handler=handler),
        registry=RuntimeTaskRegistry(),
        task_id="checkpoint",
        run_id="checkpoint",
        output_file=output_file,
    )
    assert result.value == ["one", "two"]

from __future__ import annotations

from clawcodex_ext.cron_system.runs import create_queued_run_for_task
from clawcodex_ext.cron_system.status import build_autonomy_runs, build_autonomy_status
from clawcodex_ext.cron_system.tasks import add_cron_task


def test_autonomy_status_shows_jobs_and_runs(tmp_path) -> None:
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", durable=True, created_at=1_000)
    run = create_queued_run_for_task(tmp_path, task, queued_at=2_000)

    output = build_autonomy_status(tmp_path)

    assert "Autonomy status" in output
    assert task.id in output
    assert run is not None
    assert run.id in output
    assert "queued" in output


def test_autonomy_runs_empty_message(tmp_path) -> None:
    assert build_autonomy_runs(tmp_path) == "No scheduled-task runs."

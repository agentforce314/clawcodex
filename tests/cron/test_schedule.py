from __future__ import annotations

from clawcodex_ext.cron_system.schedule import (
    format_cron_task_detail,
    format_manual_fire_result,
    get_cron_task_detail,
    manual_fire_cron_task,
)
from clawcodex_ext.cron_system.tasks import add_cron_task


def test_get_cron_task_detail_formats_trigger_fields(tmp_path) -> None:
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", durable=True, created_at=1_000)

    detail = get_cron_task_detail(tmp_path, task.id)
    assert detail is not None
    output = format_cron_task_detail(detail)

    assert f"Trigger: {task.id}" in output
    assert "Status: enabled" in output
    assert "Schedule:" in output
    assert "Agent: —" in output
    assert "Next run:" in output
    assert "Last run:" in output
    assert "Created: 1000" in output
    assert "Prompt: ping" in output


def test_manual_fire_creates_run_and_formats_run_id(tmp_path) -> None:
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", durable=True, created_at=1_000)

    run = manual_fire_cron_task(tmp_path, task.id, current_dir=tmp_path)
    output = format_manual_fire_result(task.id, run)

    assert run is not None
    assert f"Trigger {task.id} fired." in output
    assert f"Run ID: {run.id}" in output


def test_manual_fire_deduplicates_across_cli_processes(tmp_path) -> None:
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", durable=True, created_at=1_000)

    first = manual_fire_cron_task(tmp_path, task.id, current_dir=tmp_path)
    second = manual_fire_cron_task(tmp_path, task.id, current_dir=tmp_path)

    assert first is not None
    assert first.owner_process_id is None
    assert second is None


def test_manual_fire_unknown_task_returns_none(tmp_path) -> None:
    assert manual_fire_cron_task(tmp_path, "missing") is None

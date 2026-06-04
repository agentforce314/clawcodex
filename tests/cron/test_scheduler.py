from __future__ import annotations

from dataclasses import replace

from clawcodex_ext.cron_system.notifications import build_missed_task_notification
from clawcodex_ext.cron_system.runs import read_cron_runs
from clawcodex_ext.cron_system.scheduler import CronScheduler
from clawcodex_ext.cron_system.tasks import add_cron_task, read_cron_tasks, write_cron_tasks


def test_check_once_fires_due_one_shot_and_deletes_it(tmp_path) -> None:
    fired: list[str] = []
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="once", recurring=False, created_at=1_000)
    write_cron_tasks(tmp_path, [replace(task, next_fire_at=2_000)])
    scheduler = CronScheduler(tmp_path, on_fire=fired.append)
    due = scheduler.check_once(at_ms=3_000)
    assert [task.prompt for task in due] == ["once"]
    assert fired == ["once"]
    assert read_cron_tasks(tmp_path) == []


def test_check_once_fires_recurring_and_updates_last_fire(tmp_path) -> None:
    fired: list[str] = []
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", recurring=True, created_at=1_000)
    write_cron_tasks(tmp_path, [replace(task, next_fire_at=2_000)])
    scheduler = CronScheduler(tmp_path, on_fire=fired.append)
    scheduler.check_once(at_ms=3_000)
    tasks = read_cron_tasks(tmp_path)
    assert fired == ["ping"]
    assert len(tasks) == 1
    assert tasks[0].last_fired_at == 3_000


def test_check_once_prefers_task_callback_over_prompt_callback(tmp_path) -> None:
    prompts: list[str] = []
    fired = []
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", recurring=False, created_at=1_000)
    write_cron_tasks(tmp_path, [replace(task, next_fire_at=2_000)])
    scheduler = CronScheduler(tmp_path, on_fire=prompts.append, on_fire_task=lambda task, run: fired.append((task, run)))

    scheduler.check_once(at_ms=3_000)

    assert prompts == []
    assert [task.prompt for task, _run in fired] == ["ping"]
    assert fired[0][1].task_id == task.id


def test_check_once_skips_due_task_with_active_run(tmp_path) -> None:
    fired: list[str] = []
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", recurring=True, created_at=1_000)
    write_cron_tasks(tmp_path, [replace(task, next_fire_at=2_000)])
    scheduler = CronScheduler(tmp_path, on_fire=fired.append)

    first_due = scheduler.check_once(at_ms=3_000)
    write_cron_tasks(tmp_path, [replace(read_cron_tasks(tmp_path)[0], next_fire_at=4_000)])
    second_due = scheduler.check_once(at_ms=5_000)

    assert [item.id for item in first_due] == [task.id]
    assert second_due == []
    assert fired == ["ping"]
    runs = read_cron_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].status == "queued"


def test_notify_missed_once_reports_and_removes_due_one_shot(tmp_path) -> None:
    notifications: list[str] = []
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="once", recurring=False, created_at=1_000)
    write_cron_tasks(tmp_path, [replace(task, next_fire_at=2_000)])
    scheduler = CronScheduler(tmp_path, on_fire=lambda _prompt: None, on_missed=lambda _tasks, message: notifications.append(message))

    missed = scheduler.notify_missed_once(at_ms=3_000)

    assert [task.id for task in missed] == [task.id]
    assert task.id in notifications[0]
    assert read_cron_tasks(tmp_path) == []


def test_missed_notification_uses_safe_fence(tmp_path) -> None:
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="contains ``` fence", recurring=False, created_at=1_000)
    notification = build_missed_task_notification([task])
    assert "````" in notification
    assert task.id in notification

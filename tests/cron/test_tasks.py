from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from clawcodex_ext.cron_system.models import CronJitterConfig, SCHEDULED_TASKS_RELATIVE_PATH
from clawcodex_ext.cron_system.tasks import (
    add_cron_task,
    mark_cron_tasks_fired,
    prune_expired_recurring_tasks,
    read_all_cron_tasks,
    read_cron_tasks,
    read_session_cron_tasks,
    remove_cron_tasks,
    write_cron_tasks,
)


def test_add_list_delete_persisted_tasks(tmp_path) -> None:
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", created_at=1_000)
    assert len(task.id) == 8
    assert (tmp_path / SCHEDULED_TASKS_RELATIVE_PATH).exists()
    assert read_cron_tasks(tmp_path) == [task]
    assert remove_cron_tasks(tmp_path, task.id) is True
    assert read_cron_tasks(tmp_path) == []


def test_invalid_persisted_entries_are_skipped(tmp_path) -> None:
    path = tmp_path / SCHEDULED_TASKS_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"tasks": [{"id": "bad"}, {"id": "ok", "cron": "*/5 * * * *", "prompt": "ping"}]}),
        encoding="utf-8",
    )
    tasks = read_cron_tasks(tmp_path)
    assert [task.id for task in tasks] == ["ok"]


def test_session_tasks_do_not_write_persisted_file(tmp_path) -> None:
    session_store = {}
    task = add_cron_task(
        tmp_path,
        cron="*/5 * * * *",
        prompt="session ping",
        durable=False,
        session_store=session_store,
        created_at=1_000,
    )

    assert read_session_cron_tasks(session_store) == [task]
    assert read_cron_tasks(tmp_path) == []
    assert not (tmp_path / SCHEDULED_TASKS_RELATIVE_PATH).exists()


def test_read_all_cron_tasks_merges_session_and_persisted_tasks(tmp_path) -> None:
    session_store = {}
    session_task = add_cron_task(
        tmp_path,
        cron="*/5 * * * *",
        prompt="session ping",
        durable=False,
        session_store=session_store,
        created_at=1_000,
    )
    durable_task = add_cron_task(
        tmp_path,
        cron="*/10 * * * *",
        prompt="durable ping",
        durable=True,
        created_at=1_000,
    )

    assert {task.id for task in read_all_cron_tasks(tmp_path, session_store)} == {session_task.id, durable_task.id}


def test_remove_cron_tasks_deletes_session_before_persisted(tmp_path) -> None:
    session_store = {}
    task = add_cron_task(
        tmp_path,
        cron="*/5 * * * *",
        prompt="session ping",
        durable=False,
        session_store=session_store,
        created_at=1_000,
    )

    assert remove_cron_tasks(tmp_path, task.id, session_store) is True
    assert read_session_cron_tasks(session_store) == []


def test_mark_fired_updates_recurring_and_removes_one_shot(tmp_path) -> None:
    recurring = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", recurring=True, created_at=1_000)
    one_shot = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="once", recurring=False, created_at=1_000)
    mark_cron_tasks_fired(tmp_path, [recurring, one_shot], fired_at=10_000)
    tasks = read_cron_tasks(tmp_path)
    assert [task.id for task in tasks] == [recurring.id]
    assert tasks[0].last_fired_at == 10_000


def test_prune_expired_recurring_tasks(tmp_path) -> None:
    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", recurring=True, created_at=1_000)
    write_cron_tasks(tmp_path, [replace(task, expires_at=2_000)])
    removed = prune_expired_recurring_tasks(tmp_path, at_ms=3_000)
    assert [task.id for task in removed] == [task.id]
    assert read_cron_tasks(tmp_path) == []


def test_add_cron_task_serializes_concurrent_writes(tmp_path) -> None:
    def create_task(index: int) -> str:
        task = add_cron_task(
            tmp_path,
            cron="* * * * *",
            prompt=f"ping {index}",
            jitter=CronJitterConfig(enabled=False),
            created_at=1_000,
        )
        return task.id

    with ThreadPoolExecutor(max_workers=16) as pool:
        task_ids = list(pool.map(create_task, range(40)))

    tasks = read_cron_tasks(tmp_path)
    assert len(task_ids) == 40
    assert len(tasks) == 40
    assert {task.id for task in tasks} == set(task_ids)

from __future__ import annotations

import json

from clawcodex_ext.cron_system.runs import (
    CreateCronRunParams,
    claim_cron_run,
    create_queued_run,
    finalize_cron_run,
    read_cron_runs,
    runs_file_path,
)


def test_reads_legacy_scheduled_task_run_schema(tmp_path) -> None:
    runs_file_path(tmp_path).parent.mkdir(parents=True)
    runs_file_path(tmp_path).write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "id": "run1",
                        "task_id": "task1",
                        "prompt": "ping",
                        "status": "queued",
                        "queued_at": 1000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    runs = read_cron_runs(tmp_path)

    assert len(runs) == 1
    assert runs[0].id == "run1"
    assert runs[0].source_id == "task1"
    assert runs[0].prompt_preview == "ping"
    assert runs[0].trigger == "scheduled-task"


def test_create_queued_run_writes_rich_source_metadata(tmp_path) -> None:
    run = create_queued_run(
        tmp_path,
        CreateCronRunParams(
            task_id="task1",
            prompt="run the scheduled task",
            cron="*/5 * * * *",
            queued_at=1000,
            source_id="task1",
            source_label="Scheduled task label",
            root_dir=str(tmp_path),
            current_dir=str(tmp_path / "subdir"),
            owner_session_id="session1",
        ),
    )

    assert run is not None
    stored = read_cron_runs(tmp_path)[0]
    assert stored.id == run.id
    assert stored.source_id == "task1"
    assert stored.source_label == "Scheduled task label"
    assert stored.prompt_preview == "run the scheduled task"
    assert stored.root_dir == str(tmp_path)
    assert stored.current_dir == str(tmp_path / "subdir")
    assert stored.owner_session_id == "session1"


def test_active_source_dedup_prevents_duplicate_runs(tmp_path) -> None:
    first = create_queued_run(tmp_path, CreateCronRunParams(task_id="task1", prompt="ping", queued_at=1000))
    second = create_queued_run(tmp_path, CreateCronRunParams(task_id="task1", prompt="ping", queued_at=2000))

    assert first is not None
    assert second is None
    assert len(read_cron_runs(tmp_path)) == 1


def test_claim_and_finalize_run_lifecycle(tmp_path) -> None:
    run = create_queued_run(tmp_path, CreateCronRunParams(task_id="task1", prompt="ping", queued_at=1000))
    assert run is not None

    claimed = claim_cron_run(tmp_path, run.id, timestamp=2000)
    finished = finalize_cron_run(tmp_path, run.id, "failed", timestamp=3000, error="boom")

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.started_at == 2000
    assert finished is not None
    assert finished.status == "failed"
    assert finished.completed_at == 3000
    assert finished.ended_at == 3000
    assert finished.error == "boom"

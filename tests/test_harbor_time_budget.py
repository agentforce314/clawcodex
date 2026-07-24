from __future__ import annotations

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "eval" / "harbor"))

from time_budget import build_deadline_prompt, resolve_agent_timeout_seconds


def test_resolve_agent_timeout_uses_agent_multiplier(tmp_path: Path) -> None:
    task = tmp_path / "task.toml"
    lock = tmp_path / "lock.json"
    task.write_text("[agent]\ntimeout_sec = 900\n", encoding="utf-8")
    lock.write_text(
        json.dumps(
            {
                "timeout_multiplier": 3,
                "agent_timeout_multiplier": 2,
            }
        ),
        encoding="utf-8",
    )

    assert resolve_agent_timeout_seconds(task, lock) == 1800


def test_resolve_agent_timeout_falls_back_to_global_multiplier(
    tmp_path: Path,
) -> None:
    task = tmp_path / "task.toml"
    lock = tmp_path / "lock.json"
    task.write_text("[agent]\ntimeout_sec = 1200\n", encoding="utf-8")
    lock.write_text('{"timeout_multiplier": 1.5}', encoding="utf-8")

    assert resolve_agent_timeout_seconds(task, lock) == 1800


def test_deadline_prompt_reserves_finalization_time() -> None:
    prompt = build_deadline_prompt(1800, started_at=0)

    assert "30.0 minutes from start" in prompt
    assert "1970-01-01T00:25:30+00:00" in prompt
    assert "preserve the best valid deliverable" in prompt
    assert "repeated passing checks" in prompt


def test_invalid_timeout_inputs_disable_attachment(tmp_path: Path) -> None:
    assert (
        resolve_agent_timeout_seconds(
            tmp_path / "missing-task.toml",
            tmp_path / "missing-lock.json",
        )
        is None
    )

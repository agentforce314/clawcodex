"""TaskOutput wire format — port of TS ``mapToolResultToToolResultBlockParam``.

The tool used to fall through to ``_default_map_result_to_api``, which
``json.dumps``ed the whole result dict. The captured log then travelled as a
JSON string literal — every newline escaped to a literal ``\\n``, on one
unbroken line, next to ``pid`` / ``started_at`` / ``finished_at`` bookkeeping.
That blob went to the model AND (the transcript renders tool_result content)
to the user's screen.

Reference: typescript/src/tools/TaskOutputTool/TaskOutputTool.tsx:283-308.

The golden string in ``test_matches_the_tui_golden`` is duplicated verbatim in
ui-tui/src/__tests__/taskOutputResult.test.ts. The two runtimes cannot import
from each other, so pinning the same literal on both sides is what keeps the
serializer and the renderer from drifting apart.
"""
from __future__ import annotations

import json

from src.tool_system.tools.tasks_v2 import (
    _TASK_MAX_OUTPUT_DEFAULT,
    _TASK_MAX_OUTPUT_UPPER_LIMIT,
    _max_task_output_length,
    TaskOutputTool,
)


BUILD_LOG = (
    "EXIT=1\n"
    "#7 [internal] load build context\n"
    "#9 ERROR: process did not complete successfully: exit code: 127"
)


def _map(output: object) -> str:
    block = TaskOutputTool.map_result_to_api(output, "toolu_1")
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "toolu_1"
    return block["content"]


def _bash_result(**overrides: object) -> dict:
    task = {
        "task_id": "baa0sty3d",
        "task_type": "bash_background",
        "status": "completed",
        "exit_code": 0,
        "command": "npm run sandbox:local:build",
        "description": "Build the sandbox image with CA support",
        "output": BUILD_LOG,
        "truncated": False,
        "pid": 75947,
        "started_at": 1786432698.634229,
        "finished_at": 1786432700.10884,
    }
    task.update(overrides)
    return {"retrieval_status": "success", "task": task}


# ---------------------------------------------------------------------------
# The reported bug
# ---------------------------------------------------------------------------


def test_matches_the_tui_golden() -> None:
    """Byte-for-byte anchor, shared with the TUI renderer's test."""
    assert _map(_bash_result()) == (
        "<retrieval_status>success</retrieval_status>\n"
        "\n"
        "<task_id>baa0sty3d</task_id>\n"
        "\n"
        "<task_type>bash_background</task_type>\n"
        "\n"
        "<status>completed</status>\n"
        "\n"
        "<description>Build the sandbox image with CA support</description>\n"
        "\n"
        "<exit_code>0</exit_code>\n"
        "\n"
        "<output>\n"
        "EXIT=1\n"
        "#7 [internal] load build context\n"
        "#9 ERROR: process did not complete successfully: exit code: 127\n"
        "</output>"
    )


def test_output_keeps_real_newlines() -> None:
    content = _map(_bash_result())
    assert "\\n" not in content
    assert "\n#7 [internal] load build context\n" in content


def test_drops_wire_bookkeeping_the_model_cannot_use() -> None:
    content = _map(_bash_result())
    for noise in ("pid", "started_at", "finished_at", "75947"):
        assert noise not in content


# ---------------------------------------------------------------------------
# Part list
# ---------------------------------------------------------------------------


def test_omits_exit_code_when_absent() -> None:
    """TS emits ``<exit_code>`` only for a non-None code — a still-running
    task has none, and ``<exit_code>None</exit_code>`` would read as a
    failure code to the model."""
    assert "<exit_code>" not in _map(_bash_result(exit_code=None, status="running"))


def test_keeps_a_zero_exit_code() -> None:
    """The guard is ``is not None``, not truthiness: 0 is the success code and
    dropping it would make every clean run look like a still-running one."""
    assert "<exit_code>0</exit_code>" in _map(_bash_result(exit_code=0))


def test_omits_empty_output() -> None:
    assert "<output>" not in _map(_bash_result(output="   \n  "))


def test_preserves_indentation_on_the_first_output_line() -> None:
    """``rstrip()``, never ``strip()`` — leading indentation is content, and
    the TUI strips exactly one delimiter newline back off."""
    assert "<output>\n    indented\n</output>" in _map(_bash_result(output="    indented\n\n"))


def test_emits_truncated_only_when_the_capture_window_clipped() -> None:
    assert "<truncated>true</truncated>" in _map(_bash_result(truncated=True))
    assert "<truncated>" not in _map(_bash_result(truncated=False))


# ---------------------------------------------------------------------------
# Output bounding (port of TS formatTaskOutput) — see _format_task_output
# ---------------------------------------------------------------------------


def _big_log(lines: int = 4000) -> str:
    return "\n".join(f"#{i} step {i} " + "x" * 40 for i in range(lines))


def test_stays_under_the_persistence_threshold() -> None:
    """The reason the 32K cap is required, not redundant.

    Over ``get_persistence_threshold`` (50K) the whole content is replaced by a
    ``<persisted-output>`` wrapper around a 2KB HEAD preview, which cuts the
    part list mid-``<output>``. Both clients then fail to find the closing tag.
    Measured before this was ported: a 55KB build log rendered "(No output)".
    """
    import tempfile
    from pathlib import Path

    from src.services.tool_execution.tool_result_persistence import (
        process_tool_result_block,
    )

    result = _bash_result(output=_big_log())
    content = process_tool_result_block(
        TaskOutputTool, result, "toolu_1", tool_results_dir=Path(tempfile.mkdtemp())
    )["content"]

    assert "<persisted-output>" not in content
    assert content.rstrip().endswith("</output>")
    assert len(content) <= _TASK_MAX_OUTPUT_DEFAULT + 2_000  # cap + the part list


def test_keeps_the_tail_not_the_head() -> None:
    """A build or a test run puts the failure at the END."""
    content = _map(_bash_result(output=_big_log()))

    assert "#3999 step 3999" in content
    assert "#0 step 0 " not in content


def test_truncation_names_the_file_that_still_has_everything() -> None:
    content = _map(_bash_result(output=_big_log(), output_path="/tmp/clawcodex-bg/x.log"))

    assert "[Truncated. Full output: /tmp/clawcodex-bg/x.log]" in content


def test_truncation_header_names_the_subagent_transcript(tmp_path) -> None:
    """A subagent must get an actionable header too. TS passes
    ``getTaskOutputPath(task.id)`` unconditionally; carrying only the bash
    branch's path left subagents with a header naming no file."""
    from src.tasks.local_agent import register_async_agent
    from src.tasks_core import generate_task_id
    from src.tool_system.context import ToolContext
    from src.tool_system.tools.tasks_v2 import _runtime_task_to_output

    ctx = ToolContext(workspace_root=tmp_path)
    task_id = generate_task_id("local_agent")
    state = register_async_agent(
        agent_id=task_id,
        description="Find bugs",
        prompt="find bugs",
        agent_type="general-purpose",
        registry=ctx.runtime_tasks,
    )
    state.result_text = _big_log()

    content = _map(_runtime_task_to_output(task_id, state, ctx).output)

    assert f"[Truncated. Full output: {state.output_file}]" in content


def test_truncation_header_degrades_without_a_path() -> None:
    """Backstop for a task type carrying no path at all (``task_list``)."""
    content = _map(_bash_result(output=_big_log(), output_path=None))

    assert "[Truncated. Full output: the task's output file]" in content


def test_short_output_is_untouched() -> None:
    assert "[Truncated." not in _map(_bash_result())


def test_truncated_tag_survives_because_output_is_last() -> None:
    """``<truncated>`` was unreachable when it followed ``<output>``: it is only
    ever set for a >200KB log, which is exactly the case that got cut away."""
    content = _map(_bash_result(output=_big_log(), truncated=True))

    assert "<truncated>true</truncated>" in content
    assert content.index("<truncated>") < content.index("<output>")


def test_a_tiny_limit_cannot_invert_the_bound() -> None:
    """TS computes ``slice(-availableSpace)`` unguarded, and a non-positive
    availableSpace counts from the FRONT instead — returning the whole log and
    removing the bound entirely. Measured on the unguarded port: limit=1 gave
    back 100,001 chars. That would hand the >50KB result straight back to the
    persistence wrapper, which is the failure the cap exists to prevent."""
    import os
    from unittest import mock

    from src.tool_system.tools.tasks_v2 import _format_task_output

    with mock.patch.dict(os.environ, {"TASK_MAX_OUTPUT_LENGTH": "1"}):
        out = _format_task_output("x" * 100_000, "/tmp/clawcodex-bg/t1.log")

    assert len(out) < 500
    assert out.startswith("[Truncated. Full output: /tmp/clawcodex-bg/t1.log]")
    assert "xxxx" not in out


def test_env_var_bounds() -> None:
    import os
    from unittest import mock

    with mock.patch.dict(os.environ, {"TASK_MAX_OUTPUT_LENGTH": "5000"}):
        assert _max_task_output_length() == 5_000
    # Above the ceiling clamps; junk and non-positive fall back to the default.
    with mock.patch.dict(os.environ, {"TASK_MAX_OUTPUT_LENGTH": "999999"}):
        assert _max_task_output_length() == _TASK_MAX_OUTPUT_UPPER_LIMIT
    for bad in ("0", "-1", "abc", ""):
        with mock.patch.dict(os.environ, {"TASK_MAX_OUTPUT_LENGTH": bad}):
            assert _max_task_output_length() == _TASK_MAX_OUTPUT_DEFAULT


def test_emits_error_for_a_failed_subagent(tmp_path) -> None:
    """Driven through the real projection, not a hand-built dict.

    ``_runtime_task_to_output`` did not carry ``error`` at all, so the
    ``<error>`` part was unreachable in production and a fixture-only test
    passed while a genuinely failed subagent surfaced nothing.
    """
    from src.tasks.local_agent import (
        LocalAgentTaskState,
        fail_agent_task,
        register_async_agent,
    )
    from src.tasks_core import generate_task_id
    from src.tool_system.context import ToolContext
    from src.tool_system.tools.tasks_v2 import _runtime_task_to_output

    ctx = ToolContext(workspace_root=tmp_path)
    task_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=task_id,
        description="Find bugs",
        prompt="find bugs",
        agent_type="general-purpose",
        registry=ctx.runtime_tasks,
    )
    fail_agent_task(task_id, error="subagent crashed", registry=ctx.runtime_tasks)

    runtime = ctx.runtime_tasks.get(task_id)
    assert isinstance(runtime, LocalAgentTaskState)

    content = _map(_runtime_task_to_output(task_id, runtime, ctx).output)
    assert "<error>subagent crashed</error>" in content


def test_stuck_task_hint_leads_so_it_survives_head_truncation() -> None:
    """``_guard_repeated_polls`` documents the top-level field as its primary
    channel: a >50KB result is persisted to disk and replaced by a 2KB HEAD
    preview, which keeps a leading part and drops a trailing one."""
    result = _bash_result()
    result = {"stuck_task_hint": "[stuck-task guard] stop polling", **result}
    assert _map(result).startswith("<stuck_task_hint>[stuck-task guard] stop polling</stuck_task_hint>")


def test_renders_a_missing_task() -> None:
    assert _map({"retrieval_status": "success", "task": None}) == (
        "<retrieval_status>success</retrieval_status>"
    )


def test_non_dict_output_passes_through() -> None:
    assert _map("plain text") == "plain text"


# ---------------------------------------------------------------------------
# Live results
# ---------------------------------------------------------------------------


def test_real_todo_result_is_not_json() -> None:
    """The `task_list` branch of ``_task_output_call`` — the shape the TUI
    renders as "description [status]", which needs ``<description>``."""
    result = {
        "retrieval_status": "success",
        "task": {
            "task_id": "7",
            "task_type": "task_list",
            "status": "completed",
            "description": "Ship it",
            "output": "all green",
        },
    }
    content = _map(result)

    assert "<description>Ship it</description>" in content
    assert "<output>\nall green\n</output>" in content

    try:
        json.loads(content)
    except json.JSONDecodeError:
        pass
    else:  # pragma: no cover - only reachable on a regression
        raise AssertionError("content regressed to a JSON blob")

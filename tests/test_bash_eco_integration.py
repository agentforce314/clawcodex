"""End-to-end tests: /eco through the real Bash tool + result mapper.

Contract under test (design invariants):
- eco off → byte-identical wire content to the historical assembly;
- eco on → ``ecoContent`` replaces only the stdout+stderr assembly on the
  wire; the output dict's ``stdout``/``stderr`` display fields stay raw;
  exit codes / is_error / returnCodeInterpretation are untouched;
- lossy compressions write a recovery file and reference it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.eco.state import is_eco_session, reset_eco, set_eco_session
from src.tool_system.context import ToolContext
from src.tool_system.tools.bash import bash_tool as bash_mod
from src.tool_system.tools.bash.bash_tool import (
    _assemble_bash_body,
    _bash_call,
    _bash_map_result_to_api,
)


@pytest.fixture(autouse=True)
def _fresh_eco_state():
    reset_eco()
    yield
    reset_eco()


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    c = ToolContext(workspace_root=tmp_path)
    c.cwd = tmp_path
    return c


@pytest.fixture()
def eco_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "eco-tee"
    monkeypatch.setattr(bash_mod, "_eco_tee_dir", lambda _ctx: d)
    return d


def _wire_content(result) -> str:
    block = _bash_map_result_to_api(result.output, "toolu_test")
    return block["content"]


NOISY_INSTALL = (
    "printf 'Collecting requests\\n"
    "  Downloading requests-2.31.0-py3-none-any.whl (62 kB)\\n"
    "Requirement already satisfied: idna in ./venv\\n"
    "Successfully installed requests-2.31.0\\n'"
)


def test_eco_off_leaves_wire_content_untouched(ctx: ToolContext, eco_dir: Path):
    result = _bash_call({"command": NOISY_INSTALL}, ctx)
    assert "ecoContent" not in result.output
    content = _wire_content(result)
    assert "Downloading requests" in content
    assert "Successfully installed requests-2.31.0" in content


def test_eco_on_compresses_wire_and_keeps_raw_fields(ctx: ToolContext, eco_dir: Path):
    set_eco_session(True)
    # noise_strip groups are command-scoped; the trailing comment makes this
    # printf-simulated output count as a pip-family command.
    result = _bash_call({"command": f"{NOISY_INSTALL}  # pip install requests"}, ctx)

    # Wire content (which the TUI transcript also renders): compressed.
    assert result.output.get("ecoFilter") == "noise-strip"
    content = _wire_content(result)
    assert content == "Successfully installed requests-2.31.0"

    # Raw output-dict fields survive for persistence/consumers.
    assert "Downloading requests" in result.output["stdout"]

    # Execution semantics untouched.
    assert result.output["exit_code"] == 0
    assert result.is_error in (False, None)
    assert result.output.get("ecoSavedTokens", 0) > 0


def test_eco_large_output_head_cap_with_recovery(ctx: ToolContext, eco_dir: Path):
    set_eco_session(True)
    result = _bash_call({"command": "seq 1 1000"}, ctx)

    content = _wire_content(result)
    assert content.startswith("1\n2\n")
    assert "(+940 more lines)" in content
    assert "[see remaining: tail -n +61 " in content

    # Recovery file exists and offsets line up.
    logs = list(eco_dir.glob("*.log"))
    assert len(logs) == 1
    assert logs[0].read_text(encoding="utf-8").splitlines()[60] == "61"

    # Display stdout still carries the raw sequence (up to bash's own cap).
    assert result.output["stdout"].startswith("1\n2\n")
    assert "961" in result.output["stdout"]


def test_eco_passthrough_keeps_exact_baseline(ctx: ToolContext, eco_dir: Path):
    """A small, clean output must ship byte-identically with eco on."""
    cmd = "echo hello; echo err >&2"
    result_off = _bash_call({"command": cmd}, ctx)
    baseline = _wire_content(result_off)

    set_eco_session(True)
    result_on = _bash_call({"command": cmd}, ctx)
    assert "ecoContent" not in result_on.output
    assert _wire_content(result_on) == baseline
    assert "hello" in baseline and "err" in baseline


def test_eco_failing_command_semantics_unchanged(ctx: ToolContext, eco_dir: Path):
    set_eco_session(True)
    result = _bash_call({"command": "echo boom >&2; exit 3"}, ctx)
    assert result.output["exit_code"] == 3
    content = _wire_content(result)
    assert "boom" in content
    # returnCodeInterpretation (if any) still appended by the mapper.
    interp = result.output.get("returnCodeInterpretation")
    if interp:
        assert interp in content


def test_eco_lossy_hit_without_tee_falls_back(ctx: ToolContext, monkeypatch):
    """Tee dir unavailable → head-cap (lossy) must NOT fire."""
    monkeypatch.setattr(bash_mod, "_eco_tee_dir", lambda _ctx: None)
    set_eco_session(True)
    result = _bash_call({"command": "seq 1 1000"}, ctx)
    assert "ecoContent" not in result.output
    assert "1000" in _wire_content(result)


def test_mapper_appends_interpretation_after_eco_content():
    output = {
        "cwd": "/w",
        "exit_code": 1,
        "stdout": "raw out",
        "stderr": "",
        "ecoContent": "compact",
        "returnCodeInterpretation": "grep: no matches found",
    }
    block = _bash_map_result_to_api(output, "toolu_x")
    assert block["content"] == "compact\ngrep: no matches found"


def test_assemble_bash_body_matches_mapper_for_plain_results():
    output = {"stdout": "\n\n  \nout line\n", "stderr": " err line \n"}
    body = _assemble_bash_body(output["stdout"], output["stderr"])
    block = _bash_map_result_to_api(dict(output), "toolu_y")
    assert block["content"] == body == "out line\nerr line"

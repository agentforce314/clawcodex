"""Group B — Runtime substitutions (covers DEV-2, var-sub portion).

Tests every transform in ``runtime_substitution.render_skill_prompt`` in
isolation:

  - base-dir header is prepended for disk skills, omitted for bundled
  - ``${CLAUDE_SKILL_DIR}`` resolves to the skill's base dir
  - ``${CLAUDE_SESSION_ID}`` resolves to the active session id (and to
    the empty string when unknown)
  - argument substitution still composes correctly with the prepended
    header (i.e., the order ``header → arg sub`` doesn't break either)

Shell-execution-in-prompt is a separate file (test_skills_shell_exec.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from src.skills.bundled_skills import (
    BundledSkillDefinition,
    clear_bundled_skills,
    register_bundled_skill,
)
from src.skills.loader import (
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
)
from src.skills.runtime_substitution import (
    prepend_base_dir_header,
    render_skill_prompt,
    substitute_session_id,
    substitute_skill_dir,
)
from src.tool_system.context import ToolContext
from src.tool_system.tools import SkillTool


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in (
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_MANAGED_CONFIG_DIR",
        "CLAWCODEX_SKILLS_DIR",
        "CLAUDE_SKILLS_DIR",
        "CLAWCODEX_MANAGED_SKILLS_DIR",
        "CLAUDE_CODE_BARE_MODE",
        "CLAUDE_CODE_DISABLE_POLICY_SKILLS",
        "CLAUDE_CODE_ADDITIONAL_DIRECTORIES",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_MANAGED_CONFIG_DIR", str(tmp_path / "managed"))
    yield home


@pytest.fixture(autouse=True)
def _clean_skill_state() -> Iterator[None]:
    clear_skill_caches()
    clear_dynamic_skills()
    clear_skill_registry()
    clear_bundled_skills()
    yield
    clear_skill_caches()
    clear_dynamic_skills()
    clear_skill_registry()
    clear_bundled_skills()


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# ======================================================================
# Pure-function tests (each transform in isolation)
# ======================================================================


class TestPrependBaseDirHeader:
    def test_with_base_dir_prepends_header(self) -> None:
        result = prepend_base_dir_header("body", "/path/to/skill")
        assert result == "Base directory for this skill: /path/to/skill\n\nbody"

    def test_without_base_dir_is_noop(self) -> None:
        assert prepend_base_dir_header("body", None) == "body"
        assert prepend_base_dir_header("body", "") == "body"


class TestSubstituteSkillDir:
    def test_replaces_placeholder(self) -> None:
        result = substitute_skill_dir(
            "Path: ${CLAUDE_SKILL_DIR}/script.sh", "/abs/skill"
        )
        assert result == "Path: /abs/skill/script.sh"

    def test_replaces_all_occurrences(self) -> None:
        result = substitute_skill_dir(
            "${CLAUDE_SKILL_DIR} | ${CLAUDE_SKILL_DIR}", "/x"
        )
        assert result == "/x | /x"

    def test_no_base_dir_leaves_placeholder(self) -> None:
        # Bundled skills (no base_dir) keep the literal placeholder
        # rather than emitting a stray empty-string substitution.
        assert (
            substitute_skill_dir("Path: ${CLAUDE_SKILL_DIR}", None)
            == "Path: ${CLAUDE_SKILL_DIR}"
        )

    def test_normalizes_backslashes(self) -> None:
        # Windows compat: backslashes get flipped so embedded shell
        # commands don't see them as escape sequences.
        result = substitute_skill_dir("X=${CLAUDE_SKILL_DIR}", r"C:\Users\me\skill")
        assert "\\" not in result
        assert "C:/Users/me/skill" in result


class TestSubstituteSessionId:
    def test_replaces_placeholder(self) -> None:
        assert (
            substitute_session_id("S=${CLAUDE_SESSION_ID}", "abc-123")
            == "S=abc-123"
        )

    def test_unknown_session_substitutes_empty(self) -> None:
        # Matches TS' falsy-getSessionId() behavior.
        assert substitute_session_id("S=${CLAUDE_SESSION_ID}", None) == "S="
        assert substitute_session_id("S=${CLAUDE_SESSION_ID}", "") == "S="


# ======================================================================
# Combined renderer tests (disk skill — full transform chain)
# ======================================================================


def test_render_disk_skill_prepends_base_dir_header() -> None:
    out = render_skill_prompt(
        body="hello",
        args=None,
        base_dir="/abs/skill",
        argument_names=[],
        session_id="sess-1",
        loaded_from="project",
    )
    assert out.startswith("Base directory for this skill: /abs/skill\n\n")
    assert out.endswith("hello")


def test_render_bundled_skill_skips_base_dir_header() -> None:
    # Bundled skills don't ship a `base_dir` — the header must NOT
    # appear, mirroring the TS behavior that gates the prepend on the
    # presence of a `files` directory.
    out = render_skill_prompt(
        body="bundled body",
        args=None,
        base_dir=None,
        argument_names=[],
        session_id="sess-1",
        loaded_from="bundled",
    )
    assert "Base directory for this skill" not in out
    assert out == "bundled body"


def test_render_substitutes_skill_dir_and_session_id_together() -> None:
    out = render_skill_prompt(
        body="dir=${CLAUDE_SKILL_DIR} sess=${CLAUDE_SESSION_ID}",
        args=None,
        base_dir="/abs/skill",
        argument_names=[],
        session_id="sess-xyz",
        loaded_from="project",
    )
    # Header + body with both placeholders resolved.
    assert "dir=/abs/skill" in out
    assert "sess=sess-xyz" in out
    assert out.startswith("Base directory for this skill: /abs/skill")


def test_render_argument_substitution_works_after_prepend() -> None:
    # Body has `$0` (0-indexed shorthand for the first parsed arg);
    # args provided. After base-dir prepend the body should still see
    # argument substitution apply correctly.
    out = render_skill_prompt(
        body="Hello $0",
        args="world",
        base_dir="/abs/skill",
        argument_names=[],
        session_id=None,
        loaded_from="project",
    )
    assert "Hello world" in out
    assert out.startswith("Base directory for this skill:")


def test_render_named_argument_substitution_works_after_prepend() -> None:
    out = render_skill_prompt(
        body="Hello $name",
        args="alice",
        base_dir="/abs/skill",
        argument_names=["name"],
        session_id=None,
        loaded_from="project",
    )
    assert "Hello alice" in out


# ======================================================================
# End-to-end through SkillTool — verifies the wiring (not just the
# helper functions) substitutes everything correctly when invoked the
# way the model would.
# ======================================================================


def test_skilltool_invocation_substitutes_all_placeholders(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    skill_dir = project / ".claude" / "skills" / "showctx"
    _write_skill(
        skill_dir / "SKILL.md",
        "---\n"
        "description: Show context placeholders\n"
        "arguments: [name]\n"
        "---\n"
        "Hi $name from ${CLAUDE_SKILL_DIR} (session ${CLAUDE_SESSION_ID})",
    )

    ctx = ToolContext(workspace_root=project)
    ctx.session_id = "S-12345"

    result = SkillTool.call({"skill": "showctx", "args": "ada"}, ctx)
    out = result.output
    assert out["success"] is True
    prompt = out["prompt"]

    # All four expected substitutions:
    assert prompt.startswith(
        "Base directory for this skill:"
    ), f"missing base-dir header in: {prompt!r}"
    assert str(skill_dir.resolve()) in prompt or str(skill_dir) in prompt
    assert "Hi ada from" in prompt
    assert "(session S-12345)" in prompt
    # Placeholders fully resolved (no literal ${...} survivors).
    assert "${CLAUDE_SKILL_DIR}" not in prompt
    assert "${CLAUDE_SESSION_ID}" not in prompt


def test_skilltool_unknown_session_id_renders_empty(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "sess" / "SKILL.md",
        "---\ndescription: shows session\n---\nsession=[${CLAUDE_SESSION_ID}]",
    )

    ctx = ToolContext(workspace_root=project)
    # Default ToolContext.session_id is None.
    assert ctx.session_id is None

    result = SkillTool.call({"skill": "sess"}, ctx)
    prompt = result.output["prompt"]
    assert "session=[]" in prompt


def test_bundled_skill_invocation_does_not_get_base_dir_header(
    tmp_path: Path, isolated_home: Path
) -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            name="bcheck",
            description="bundled prompt-builder",
            get_prompt_for_command=lambda args: f"bundled body :: args={args}",
        )
    )
    project = tmp_path / "proj"
    project.mkdir()
    ctx = ToolContext(workspace_root=project)
    result = SkillTool.call({"skill": "bcheck", "args": "x"}, ctx)
    prompt = result.output["prompt"]
    # Bundled skills route through their own get_prompt_for_command
    # callable; the header (which is render_skill_prompt's job) must
    # not appear.
    assert "Base directory for this skill" not in prompt
    assert "bundled body :: args=x" in prompt

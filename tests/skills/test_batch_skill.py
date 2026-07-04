"""SKILLS-1 — the ``/batch`` bundled skill (verbatim port of bundled/batch.ts).

Registered unconditionally in TS ``initBundledSkills``; was absent in the
port. Pins registration + the missing-instruction / not-a-git-repo guards +
the built prompt.
"""
from __future__ import annotations

import pytest

import src.context_system.git_context as gc
from src.skills.bundled import init_bundled_skills
from src.skills.bundled.batch import (
    WORKER_INSTRUCTIONS,
    _build_prompt,
    _get_prompt_for_command,
)
from src.skills.bundled_skills import (
    clear_bundled_skills,
    get_bundled_skill_by_name,
)


@pytest.fixture()
def _fresh_registry():
    clear_bundled_skills()
    init_bundled_skills()
    yield
    clear_bundled_skills()


class TestRegistration:
    def test_registered_with_fields(self, _fresh_registry):
        b = get_bundled_skill_by_name("batch")
        assert b is not None
        assert b.user_invocable is True
        assert b.disable_model_invocation is True
        assert b.argument_hint == "<instruction>"
        assert "parallel" in b.description.lower()


class TestGuards:
    def test_missing_instruction(self):
        out = _get_prompt_for_command("")
        assert "Provide an instruction" in out
        assert "/batch migrate from react to vue" in out
        # whitespace-only is also "missing"
        assert "Provide an instruction" in _get_prompt_for_command("   ")

    def test_not_a_git_repo(self, monkeypatch):
        monkeypatch.setattr(gc, "get_is_git", lambda cwd=None: False)
        out = _get_prompt_for_command("do a thing")
        assert "not a git repository" in out
        assert "/batch" in out

    def test_git_repo_builds_prompt(self, monkeypatch):
        monkeypatch.setattr(gc, "get_is_git", lambda cwd=None: True)
        out = _get_prompt_for_command("migrate lodash to native")
        assert "Batch: Parallel Work Orchestration" in out
        assert "migrate lodash to native" in out


class TestPrompt:
    def test_contains_tool_names_and_worktree(self):
        out = _build_prompt("some instruction")
        for needle in (
            "EnterPlanMode",
            "ExitPlanMode",
            "Agent",
            "AskUserQuestion",
            "Skill",
            'isolation: "worktree"',
            "run_in_background",
        ):
            assert needle in out, needle

    def test_agent_count_range(self):
        out = _build_prompt("x")
        assert "5" in out and "30" in out
        assert "5–30" in out

    def test_worker_instructions_embedded_verbatim(self):
        out = _build_prompt("x")
        assert WORKER_INSTRUCTIONS in out
        # the worker steps
        assert 'skill: "simplify"' in WORKER_INSTRUCTIONS
        assert "PR: <url>" in WORKER_INSTRUCTIONS

    def test_instruction_interpolated(self):
        out = _build_prompt("REPLACE_ME_TOKEN")
        assert "## User Instruction\n\nREPLACE_ME_TOKEN" in out

    def test_golden_length_and_anchors(self):
        # Golden pin (critic NIT): a length + boundary snapshot guards against
        # silent whitespace/section drift on future edits (byte-identity to TS
        # was verified externally by the critic).
        out = _build_prompt("X")
        assert out.startswith("# Batch: Parallel Work Orchestration\n\n")
        assert out.rstrip().endswith('"22/24 units landed as PRs").')
        assert out.count("## Phase") == 3  # Research/Plan, Spawn, Track
        # stable size modulo the 1-char instruction (regression canary)
        assert 4830 <= len(out) <= 4860, len(out)


class TestGuardOrder:
    def test_missing_instruction_wins_over_git_check(self, monkeypatch):
        # critic NIT: empty args must short-circuit to MISSING_INSTRUCTION
        # BEFORE the git check runs (order parity with TS 112→116). If git were
        # checked first, a non-git dir + empty args would wrongly return the
        # git message.
        called = {"git": False}

        def _spy(cwd=None):
            called["git"] = True
            return False

        monkeypatch.setattr(gc, "get_is_git", _spy)
        out = _get_prompt_for_command("   ")
        assert "Provide an instruction" in out
        assert called["git"] is False  # git-check never reached

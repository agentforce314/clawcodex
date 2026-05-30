"""Tests for the security-review builtin (Phase 1.5).

Covers the factory wiring (ant branch → static plugin text) and the real
marketplace-private path, which executes the four read-only ``git`` blocks at
prompt-build time in a throwaway git repo and splices their output into the prompt.
Port of ``commands/security-review.ts``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from src.command_system import (
    SECURITY_REVIEW_COMMAND,
    create_command_context,
    get_commands,
)

# Literal inline shell markers from SECURITY_REVIEW_MARKDOWN — must all be gone after exec.
GIT_MARKERS = [
    "!`git status`",
    "!`git diff --name-only origin/HEAD...`",
    "!`git log --no-decorate origin/HEAD...`",
    "!`git diff origin/HEAD...`",
]


def _init_git_repo(path: Path) -> None:
    """Create a minimal git repo with one commit and a clean working tree.

    Local identity + disabled gpg signing keep ``git commit`` working in CI sandboxes
    that have no global git config. The hermetic env (``GIT_CONFIG_NOSYSTEM``, a
    ``/dev/null`` global config, ``HOME`` pinned to the temp dir) also neutralizes any
    hostile system/global ``core.hooksPath`` / ``init.templateDir`` that could otherwise
    inject a failing commit hook. After the commit, ``git status`` reports a clean tree
    (no remote, so the three ``origin/HEAD...`` blocks deliberately fail).
    """
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "HOME": str(path),
    }

    def run(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    run("init")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test User")
    run("config", "commit.gpgsign", "false")
    (path / "app.py").write_text("print('hello')\n")
    run("add", "-A")
    run("commit", "-m", "initial commit")


async def test_ant_branch_returns_static_plugin_text(monkeypatch, tmp_path):
    monkeypatch.setenv("USER_TYPE", "ant")
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)

    result = await SECURITY_REVIEW_COMMAND.get_prompt_for_command("", ctx)

    assert len(result) == 1
    text = result[0]["text"]
    assert (
        "openclaude plugin install security-review@claude-code-marketplace" in text
    )
    assert "/security-review:security-review" in text
    # The ant branch must NOT shell out — none of the markdown body/exec appears.
    assert "OBJECTIVE" not in text
    assert "nothing to commit" not in text


async def test_private_branch_runs_git_blocks(monkeypatch, tmp_path):
    monkeypatch.delenv("USER_TYPE", raising=False)
    _init_git_repo(tmp_path)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)

    result = await SECURITY_REVIEW_COMMAND.get_prompt_for_command("", ctx)

    assert len(result) == 1
    text = result[0]["text"]

    # Every inline marker was executed and spliced away.
    for marker in GIT_MARKERS:
        assert marker not in text, f"marker still present: {marker}"

    # `git status` ran in the temp repo and produced its real (clean-tree) output.
    # These phrases appear nowhere in the static markdown body, so they prove exec.
    assert "nothing to commit" in text or "working tree clean" in text

    # The three `origin/HEAD...` blocks have no remote -> git exits 128 -> each renders
    # the INLINE error form `[Error: ...]` (NOT `[Error]`: in Python
    # `'[Error]' in '[Error: fatal…]'` is False). This is the non-zero-exit branch of
    # make_bash_shell_executor (exec resilience), proving failures render inline without
    # aborting the build.
    assert "[Error:" in text
    assert text.count("[Error:") >= 3

    # Static body survives the transform.
    assert "OBJECTIVE" in text
    assert "START ANALYSIS" in text

    # Frontmatter was stripped (parse_frontmatter -> body only).
    assert "allowed-tools:" not in text


async def test_args_are_ignored(monkeypatch, tmp_path):
    # Faithfulness guard (§0.1): security-review ignores args — no skill-style
    # `ARGUMENTS:` append, no `${…}` substitution.
    monkeypatch.delenv("USER_TYPE", raising=False)
    _init_git_repo(tmp_path)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)

    result = await SECURITY_REVIEW_COMMAND.get_prompt_for_command("ignored-pr-123", ctx)

    text = result[0]["text"]
    assert "ignored-pr-123" not in text
    assert "ARGUMENTS:" not in text


def test_security_review_listed_in_unfiltered_commands():
    # §3.4 / layer check: security-review belongs in the normal LOCAL command set
    # (get_commands), not the remote/bridge safe-sets (it shells out to git).
    names = [c.name for c in get_commands(cwd=str(Path.cwd()))]
    assert "security-review" in names

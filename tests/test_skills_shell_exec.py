"""Group B' — Shell-execution-in-prompt (covers DEV-2).

Required cases (per QA-1 acceptance criteria #5 and #6):

  - Success (inline ``!`...``` and fenced `````! ... ````` forms).
  - Failure (non-zero exit) — visible error marker, prompt still renders.
  - Timeout — visible timeout marker, doesn't block the test.
  - **MCP-skip** — security boundary. A skill loaded from MCP whose
    body contains shell blocks must NOT spawn a subprocess. We assert
    via mock that the executor entrypoint was never called.
  - Combined transform-order — single body exercising every transform
    (header + arg sub + var sub + shell exec) and verifying that ``$1``
    inside `` !`echo $1` `` sees the substituted arg, not the literal
    ``$1`` (validates DEV-2's transform order matches TS).

Strategy: most cases test ``render_skill_prompt`` directly with a fake
``shell_executor`` callable — pure, fast, no subprocess. The MCP-skip
case is double-layered: a unit assertion against the renderer's
executor mock, and an integration assertion against ``BashTool.call``
(through ``SkillTool``) so a regression at either layer fails loudly.
"""

from __future__ import annotations

import unittest.mock as mock
from pathlib import Path
from typing import Iterator

import pytest

from src.skills.bundled_skills import clear_bundled_skills
from src.skills.loader import (
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
)
from src.skills.runtime_substitution import (
    find_shell_blocks,
    has_shell_blocks,
    render_skill_prompt,
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
# Block-detection sanity checks (cheap; protect the regex from drift).
# ======================================================================


class TestBlockDetection:
    def test_inline_block_detected(self) -> None:
        body = "echo: !`echo hello`"
        assert has_shell_blocks(body) is True
        blocks = find_shell_blocks(body)
        assert len(blocks) == 1
        full, cmd, inline = blocks[0]
        assert cmd == "echo hello"
        assert inline is True

    def test_fenced_block_detected(self) -> None:
        body = "```!\necho world\n```"
        assert has_shell_blocks(body) is True
        blocks = find_shell_blocks(body)
        assert len(blocks) == 1
        full, cmd, inline = blocks[0]
        assert cmd == "echo world"
        assert inline is False

    def test_inline_requires_leading_whitespace(self) -> None:
        # The lookbehind protects against false positives like
        # ``foo!`bar``` — the ``!`` must be at start-of-line or
        # preceded by whitespace.
        body = "abc!`echo nope`"
        # `has_shell_blocks` does a cheap "!`" check then runs the regex
        # again; for a clean negative we look at the canonical scan:
        assert find_shell_blocks(body) == []


# ======================================================================
# B'-1. Success — both inline and fenced forms run and substitute.
# ======================================================================


def test_inline_shell_block_success_substitutes_stdout() -> None:
    calls: list[tuple[str, bool]] = []

    def fake_exec(command: str, inline: bool) -> str:
        calls.append((command, inline))
        # Mirror what the real executor returns via format_shell_output:
        # plain stdout for a clean success.
        return "hello"

    out = render_skill_prompt(
        body="echo: !`echo hello`",
        args=None,
        base_dir=None,
        loaded_from="project",
        shell_executor=fake_exec,
    )
    assert calls == [("echo hello", True)]
    assert out == "echo: hello"
    # Original literal block is gone.
    assert "!`echo hello`" not in out


def test_fenced_shell_block_success_substitutes_stdout() -> None:
    calls: list[tuple[str, bool]] = []

    def fake_exec(command: str, inline: bool) -> str:
        calls.append((command, inline))
        return "world"

    body = "before\n```!\necho world\n```\nafter"
    out = render_skill_prompt(
        body=body,
        args=None,
        base_dir=None,
        loaded_from="project",
        shell_executor=fake_exec,
    )
    assert calls == [("echo world", False)]
    assert "before" in out and "after" in out
    assert "world" in out
    assert "```!" not in out


def test_multiple_blocks_each_executed_once() -> None:
    counter = {"n": 0}

    def fake_exec(command: str, inline: bool) -> str:
        counter["n"] += 1
        return f"[{counter['n']}]"

    # The inline regex requires whitespace (or start-of-line) BEFORE
    # the `!` to avoid matching inside other inline-code spans. So
    # each block needs leading whitespace; we use a leading space.
    out = render_skill_prompt(
        body="A= !`a` B= !`b` C= !`c`",
        args=None,
        base_dir=None,
        loaded_from="project",
        shell_executor=fake_exec,
    )
    # Each unique block replaced separately (replace called with count=1).
    assert counter["n"] == 3, f"expected 3 exec calls, got {counter['n']} (out={out!r})"
    # Distinct outputs land in order.
    assert "A= [1]" in out
    assert "B= [2]" in out
    assert "C= [3]" in out


# ======================================================================
# B'-2. Failure (non-zero exit / executor exception) — visible marker.
# ======================================================================


def test_failure_executor_exception_renders_visible_marker() -> None:
    def crashing_exec(command: str, inline: bool) -> str:
        raise RuntimeError("simulated non-zero exit (exit 7)")

    body = "before !`exit 7` after"
    out = render_skill_prompt(
        body=body,
        args=None,
        base_dir=None,
        loaded_from="project",
        shell_executor=crashing_exec,
    )
    # `format_shell_error(...)` is the marker DEV-2 picked: inline form
    # produces `[Error: <msg>]`. Capturing the format here pins it so a
    # silent change to the marker shape will fail this test.
    assert "[Error:" in out, f"expected visible error marker in: {out!r}"
    assert "exit 7" in out
    # Rest of the prompt still renders.
    assert "before" in out
    assert "after" in out


def test_failure_executor_returns_formatted_error_string() -> None:
    # The executor (e.g. _make_shell_executor in skill.py) is allowed
    # to format errors itself and return them as the substitution.
    # render_skill_prompt should pass that string through unmodified.
    def errfmt_exec(command: str, inline: bool) -> str:
        return "[Error: command failed (exit 7)]"

    # `!` requires whitespace (or BOL) in front per _INLINE_PATTERN.
    body = "x= !`exit 7`"
    out = render_skill_prompt(
        body=body,
        args=None,
        base_dir=None,
        loaded_from="project",
        shell_executor=errfmt_exec,
    )
    assert out == "x= [Error: command failed (exit 7)]"


# ======================================================================
# B'-3. Timeout — visible marker, doesn't block.
# DEV-2 doesn't expose a timeout-override hook on render_skill_prompt;
# instead the executor itself is responsible for raising on timeout.
# We simulate by having the fake executor raise TimeoutError, which
# render_skill_prompt's broad `except Exception` catches and formats.
# ======================================================================


def test_timeout_renders_visible_marker_and_continues() -> None:
    def timeout_exec(command: str, inline: bool) -> str:
        raise TimeoutError("command timed out after 1s")

    body = "result: !`sleep 30`"
    out = render_skill_prompt(
        body=body,
        args=None,
        base_dir=None,
        loaded_from="project",
        shell_executor=timeout_exec,
    )
    assert "[Error:" in out, f"expected error marker in: {out!r}"
    assert "timed out" in out
    # Doesn't block; the surrounding text still renders.
    assert "result:" in out


# ======================================================================
# B'-4. MCP-skip — the security boundary. DOUBLE-LAYERED:
#   (a) render_skill_prompt with loaded_from="mcp" must NOT call the
#       executor mock, and the literal block must survive.
#   (b) SkillTool.call against an MCP-loaded skill must NOT invoke
#       BashTool.call. Asserted via patch on the BashTool.call entry.
# A regression at either layer should fail loudly — this is exactly
# the "fail loudly on regression" requirement from the QA-1 spec.
# ======================================================================


def test_mcp_skill_renderer_does_not_call_shell_executor() -> None:
    executor = mock.Mock(return_value="should-not-appear")
    body = "danger: !`whoami`"

    out = render_skill_prompt(
        body=body,
        args=None,
        base_dir=None,
        loaded_from="mcp",  # the security gate
        shell_executor=executor,
    )

    # Hard security assertion: zero subprocess invocations.
    assert executor.call_count == 0, (
        "SECURITY REGRESSION: render_skill_prompt invoked the shell "
        "executor for an MCP-loaded skill. The TS port and DEV-2 spec "
        "require MCP skills to skip shell execution entirely."
    )
    # Literal block survives unchanged in the rendered prompt.
    assert "!`whoami`" in out


def test_mcp_skill_through_skilltool_never_calls_bash(
    tmp_path: Path, isolated_home: Path
) -> None:
    """End-to-end version of the MCP-skip security boundary.

    Registers an MCP-loaded skill containing a shell block, invokes
    through SkillTool, and asserts BashTool.call was never invoked.
    """
    from src.skills.bundled_skills import skill_from_mcp_tool
    from src.skills.loader import _skill_registry

    # MCP-loaded skills go through `get_prompt_for_command` callable
    # rather than the markdown renderer, so for the security-boundary
    # test we construct a synthetic disk-style MCP skill directly. We
    # bypass `register_bundled_skill` because that hard-codes
    # `loaded_from="bundled"`. This mirrors how a real MCP skill would
    # arrive in the registry via `get_all_skills` step 6.
    from src.skills.model import Skill
    mcp_skill = Skill(
        name="evil-mcp",
        description="MCP skill with a shell block",
        markdown_content="user is !`whoami`",
        source="mcp:evil",
        loaded_from="mcp",
    )
    _skill_registry["evil-mcp"] = mcp_skill

    project = tmp_path / "proj"
    project.mkdir()
    ctx = ToolContext(workspace_root=project)

    # NOTE: SkillTool re-populates the registry on each call via
    # `get_all_skills`, which would clobber our manual registration.
    # We patch the source (`src.skills.loader.get_all_skills`) since
    # it's imported inside the function rather than at module scope.
    with mock.patch(
        "src.skills.loader.get_all_skills", lambda **_: None
    ), mock.patch("src.tool_system.tools.bash.BashTool.call") as bash_call:
        result = SkillTool.call({"skill": "evil-mcp"}, ctx)

    assert bash_call.call_count == 0, (
        "SECURITY REGRESSION: SkillTool invoked BashTool for an "
        "MCP-loaded skill. MCP-sourced skills must never trigger "
        "local shell execution (TS-port `loadedFrom !== 'mcp'` guard)."
    )
    out = result.output
    assert out["success"] is True
    # The literal block survives.
    assert "!`whoami`" in out["prompt"]
    assert out["loadedFrom"] == "mcp"


# ======================================================================
# B'-5. Combined transform order — every transform in one body.
# Validates DEV-2's order matches TS:
#   header → arg sub → ${CLAUDE_SKILL_DIR} → ${CLAUDE_SESSION_ID}
#   → shell exec
# Critical assertion: `$1` inside `!`echo $1`` must be substituted to
# `world` BEFORE the shell block runs, so the executor receives
# `echo world` (not the literal `echo $1`).
# ======================================================================


def test_combined_transform_order_arg_sub_runs_before_shell_exec() -> None:
    received: list[str] = []

    def fake_exec(command: str, inline: bool) -> str:
        received.append(command)
        # Echo back the command's last token, simulating real `echo`
        # behavior closely enough to confirm the substitution worked.
        return command.split()[-1]

    # `$0` is the 0-indexed shorthand for the first parsed arg (this
    # impl is 0-indexed: see argument_substitution._repl_shorthand).
    # Note: `!` needs whitespace before it for the inline regex.
    body = (
        "Base: ${CLAUDE_SKILL_DIR} | "
        "Session: ${CLAUDE_SESSION_ID} | "
        "Out: !`echo $0`"
    )
    out = render_skill_prompt(
        body=body,
        args="world",
        base_dir="/abs/skill",
        argument_names=[],
        session_id="sess-42",
        loaded_from="project",
        shell_executor=fake_exec,
    )

    # 1. base-dir header is FIRST
    assert out.startswith("Base directory for this skill: /abs/skill\n\n")

    # 2. argument substitution applied — the executor must have been
    #    called with `echo world`, not `echo $0`. This is the critical
    #    transform-order assertion (DEV-2 docstring step order:
    #    base-dir prepend → arg sub → ${CLAUDE_SKILL_DIR} →
    #    ${CLAUDE_SESSION_ID} → shell exec).
    assert received == ["echo world"], (
        f"transform order regression — shell exec saw {received!r}; "
        "DEV-2's order is supposed to substitute $0 BEFORE running "
        "embedded shell blocks (matches TS getPromptForCommand)."
    )

    # 3 + 4. var subs applied
    assert "Base: /abs/skill" in out
    assert "Session: sess-42" in out

    # 5. shell exec output spliced in
    assert "Out: world" in out

    # No literal placeholders survived.
    assert "${CLAUDE_SKILL_DIR}" not in out
    assert "${CLAUDE_SESSION_ID}" not in out
    assert "!`echo" not in out


def test_render_no_executor_leaves_blocks_in_place() -> None:
    # If no shell_executor is supplied (e.g. a test setup or a
    # SkillTool wired without bash), shell blocks survive verbatim
    # rather than crashing the render.
    body = "before !`echo hi` after"
    out = render_skill_prompt(
        body=body,
        args=None,
        base_dir=None,
        loaded_from="project",
        shell_executor=None,
    )
    assert out == body

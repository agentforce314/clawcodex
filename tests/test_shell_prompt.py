"""Tests for the generic shell-block splicer (Phase 1.5).

``execute_shell_commands_in_prompt`` is the lower-level primitive security-review uses
instead of ``render_skill_prompt`` (§0.1). These cases pin two load-bearing properties
with pure fake executors (no subprocess):

  * **R7 — literal replacement / `$`-backref safety.** The splice uses
    ``str.replace(full_match, replacement, 1)``, which does NOT interpret ``$&`` / ``$1`` /
    ``\\1`` as backreferences. This is the justification for diverging from TS's function
    replacer; if a future refactor swaps in ``re.sub`` this test fails loudly.
  * **R8 — orchestrator resilience.** A raising executor is caught and rendered inline
    (``[Error: …]``); the build continues and never propagates.
"""
from __future__ import annotations

from src.command_system.shell_prompt import execute_shell_commands_in_prompt


def test_inline_block_replaced_with_executor_output():
    out = execute_shell_commands_in_prompt(
        "before !`echo hi` after", shell_executor=lambda cmd, inline: f"<{cmd}>"
    )
    assert out == "before <echo hi> after"


def test_no_blocks_returns_text_unchanged():
    text = "plain text, no shell blocks here"
    out = execute_shell_commands_in_prompt(text, shell_executor=lambda c, i: "X")
    assert out == text


def test_replacement_with_dollar_and_backslash_is_literal():
    # R7: none of these are interpreted as regex/replacement backreferences.
    payload = r"price=$5 $& $1 $0 \1 \g<0> ${VAR}"
    out = execute_shell_commands_in_prompt(
        "X !`emit` Y", shell_executor=lambda cmd, inline: payload
    )
    assert out == f"X {payload} Y"


def test_multiple_distinct_blocks_all_replaced():
    out = execute_shell_commands_in_prompt(
        "!`a`\n!`b`\n!`c`", shell_executor=lambda cmd, inline: cmd.upper()
    )
    assert out == "A\nB\nC"


def test_crashing_executor_is_caught_and_rendered_inline():
    # R8: orchestrator-level resilience — the error is embedded inline and the block is
    # replaced (not left raw); the call returns normally instead of raising.
    def boom(cmd, inline):
        raise RuntimeError("kaboom")

    out = execute_shell_commands_in_prompt("A !`bad` B", shell_executor=boom)
    assert "!`bad`" not in out
    assert "[Error:" in out
    assert out.startswith("A ") and out.endswith(" B")


def test_inline_flag_passed_to_executor():
    seen: list[bool] = []

    def record(cmd, inline):
        seen.append(inline)
        return "ok"

    # An inline `!`…`` token reports inline=True (the form security-review's git blocks use).
    execute_shell_commands_in_prompt("q !`x` r", shell_executor=record)
    assert seen == [True]

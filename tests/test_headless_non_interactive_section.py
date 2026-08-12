"""The cutover system-prompt builder must forward non-interactive mode.

Headless (``claude -p``) sets ``options.is_non_interactive_session`` before
the prompt is built, but ``build_effective_system_prompt`` (the headless/TUI
cutover builder) did not forward it into ``build_full_system_prompt_blocks``
— so the headless model never received the "# Non-Interactive Mode" section
and behaved as if a human would reply. Model-agnostic: any provider run
headlessly is affected; interactive callers leave the flag unset and are
unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.query.agent_loop_compat import build_effective_system_prompt
from src.tool_system.context import ToolContext


def _flatten(blocks) -> str:
    return "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))


def _ctx(non_interactive: bool) -> ToolContext:
    ctx = ToolContext(workspace_root="/tmp")
    # ``options`` is a duck-typed holder; headless sets this flag on it.
    ctx.options = SimpleNamespace(is_non_interactive_session=non_interactive)
    return ctx


def test_non_interactive_section_present_when_flag_set():
    text = _flatten(build_effective_system_prompt("", _ctx(True)))
    assert "# Non-Interactive Mode" in text


def test_non_interactive_section_absent_when_flag_unset():
    text = _flatten(build_effective_system_prompt("", _ctx(False)))
    assert "# Non-Interactive Mode" not in text


def test_missing_options_defaults_to_interactive():
    """A context with no ``options`` (or no flag) must not crash and must
    default to interactive (section absent)."""
    ctx = ToolContext(workspace_root="/tmp")
    text = _flatten(build_effective_system_prompt("", ctx))
    assert "# Non-Interactive Mode" not in text

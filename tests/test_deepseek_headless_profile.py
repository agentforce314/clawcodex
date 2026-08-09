"""DeepSeek headless harness profile: core tools + working-style prompt.

Measured on terminal-bench 2.1 (tb21-flash-visiontool, 2026-08-08): the
headless registry put ~40 tool schemas (~12K tokens of JSON) on DeepSeek's
OpenAI-compatible wire — which has no tool_reference deferral — while the
model's entire observed working set was 14 tools. Beyond tokens, every extra
tool is decision surface for a model whose failure mode is deliberating
instead of acting (80-95% of output tokens were reasoning_content).

The profile trims the HEADLESS registry for the ``deepseek`` provider only;
interactive sessions and every other provider keep the full surface.
``CLAWCODEX_DEEPSEEK_CORE_TOOLS=0`` restores the old behaviour, and a tool
the user explicitly names in ``--allowed-tools`` is kept even off-profile.
"""

from __future__ import annotations

import inspect

from src.context_system.prompt_assembly import build_full_system_prompt_blocks
from src.entrypoints import headless as headless_mod
from src.providers.deepseek_provider import DeepSeekProvider


class _NotDeepSeek:
    is_deepseek = False


def _flatten(blocks) -> str:
    return "\n".join(b.get("text", "") for b in blocks)


# --- working-style prompt section ------------------------------------------


def test_working_style_section_present_for_deepseek():
    text = _flatten(
        build_full_system_prompt_blocks(
            cwd="/tmp", provider=DeepSeekProvider.__new__(DeepSeekProvider)
        )
    )
    assert "# Working Style" in text
    # The section's two core behaviours: act-don't-deliberate and
    # verify-before-finishing.
    assert "Bias to action" in text
    assert "done only when verified" in text


def test_working_style_section_absent_for_other_providers():
    text = _flatten(
        build_full_system_prompt_blocks(cwd="/tmp", provider=_NotDeepSeek())
    )
    assert "# Working Style" not in text


def test_working_style_section_absent_with_no_provider():
    text = _flatten(build_full_system_prompt_blocks(cwd="/tmp"))
    assert "# Working Style" not in text


def test_working_style_env_kill_switch(monkeypatch):
    monkeypatch.setenv("CLAWCODEX_DEEPSEEK_PROMPT", "0")
    text = _flatten(
        build_full_system_prompt_blocks(
            cwd="/tmp", provider=DeepSeekProvider.__new__(DeepSeekProvider)
        )
    )
    assert "# Working Style" not in text


def test_working_style_is_session_scope_not_request():
    """REQUEST scope would put these constant bytes in DeepSeek's relocated
    tail and re-bill them every turn — the exact bug PR #816 fixed for the
    memory doctrine."""
    from src.context_system.prompt_assembly import _build_deepseek_agent_section
    from src.context_system.system_prompt_cache import CacheScope

    section = _build_deepseek_agent_section(
        DeepSeekProvider.__new__(DeepSeekProvider)
    )
    assert section is not None
    assert section.cache_scope is CacheScope.SESSION


# --- headless core-tool profile --------------------------------------------
#
# The profile lives inline in ``run_print_mode`` (like the AskUserQuestion /
# plan-mode removals above it), so these tests pin the source contract the
# same way test_headless_tool_filter.py pins those removals.


def _headless_src() -> str:
    return inspect.getsource(headless_mod)


def test_profile_gated_on_is_deepseek_and_env():
    src = _headless_src()
    assert 'getattr(provider, "is_deepseek", False)' in src
    assert "CLAWCODEX_DEEPSEEK_CORE_TOOLS" in src


def test_profile_keeps_observed_working_set_and_drops_the_rest():
    src = _headless_src()
    block = src[src.index("CLAWCODEX_DEEPSEEK_CORE_TOOLS"):]
    block = block[: block.index("_filter_registry")]
    for kept in (
        "bash", "read", "write", "edit", "grep", "glob", "notebookedit",
        "todowrite", "webfetch", "websearch", "taskoutput", "taskstop",
        "monitor", "vision_analyze",
    ):
        assert f'"{kept}"' in block, f"core profile must keep {kept}"
    for dropped in ("croncreate", "teamcreate", "enterworktree", "skill",
                    "workflow", "agent", "sendmessage"):
        assert f'"{dropped}"' not in block, f"core profile must not list {dropped}"


def test_profile_respects_explicit_allowed_tools():
    """--allowed-tools naming an off-profile tool must keep it registered."""
    src = _headless_src()
    trim = src[src.index("CLAWCODEX_DEEPSEEK_CORE_TOOLS"):]
    trim = trim[: trim.index("_filter_registry") + 600]
    assert "allow is not None and n.lower() in allow" in trim


def test_profile_runs_before_user_allow_deny_filters():
    """User filters run after the trim so they always see the final pool."""
    src = _headless_src()
    assert src.index("CLAWCODEX_DEEPSEEK_CORE_TOOLS") < src.index(
        'keep=lambda n: n.lower() in allow'
    )

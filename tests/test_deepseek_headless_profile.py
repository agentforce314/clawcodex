"""DeepSeek headless harness profile: core tools + working-style prompt.

Measured on terminal-bench 2.1 (tb21-flash-visiontool, 2026-08-08): the
headless registry put ~44 tool schemas (~12K tokens of JSON) on DeepSeek's
OpenAI-compatible wire — which has no tool_reference deferral — while the
model's entire observed working set was 14 tools. Beyond tokens, every extra
tool is decision surface for a model whose failure mode is deliberating
instead of acting (80-95% of output tokens were reasoning_content).

The profile is OPT-IN (``CLAWCODEX_DEEPSEEK_CORE_TOOLS=1`` — a capability
removal must never hit a user's -p script silently; the harbor adapter arms
it for trial containers). These tests exercise the REAL registry + filter,
mirroring the trim block in ``run_print_mode``.
"""

from __future__ import annotations

import inspect

from src.context_system.prompt_assembly import build_full_system_prompt_blocks
from src.entrypoints import headless as headless_mod
from src.providers.deepseek_provider import DeepSeekProvider
from src.tool_system.defaults import build_default_registry


class _NotDeepSeek:
    is_deepseek = False


def _flatten(blocks) -> str:
    return "\n".join(b.get("text", "") for b in blocks)


CORE = {
    "bash", "read", "write", "edit", "grep", "glob", "notebookedit",
    "todowrite", "webfetch", "websearch", "taskoutput", "taskstop",
    "monitor", "vision_analyze",
}


def _trimmed_registry(allow: set[str] | None = None):
    """Run the same trim ``run_print_mode`` runs, on a real registry."""
    registry = build_default_registry(provider=None)
    # The pre-existing headless removals happen before the profile.
    registry.remove_tool("AskUserQuestion")
    registry.remove_tool("ExitPlanMode")
    registry.remove_tool("EnterPlanMode")
    headless_mod._filter_registry(
        registry,
        keep=lambda n: n.lower() in CORE or (allow is not None and n.lower() in allow),
    )
    return registry


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


# --- headless core-tool profile (behavioral, real registry) -----------------


def test_profile_keeps_exactly_the_observed_working_set():
    remaining = {t.name.lower() for t in _trimmed_registry().list_tools()}
    assert remaining == CORE, remaining


def test_profile_keeps_explicitly_allowed_off_profile_tool():
    """--allowed-tools naming an off-profile tool must survive the trim.

    Load-bearing clause: the later allow-filter can only REMOVE, so if the
    trim dropped the tool first, nothing could bring it back.
    """
    remaining = {
        t.name.lower()
        for t in _trimmed_registry(allow={"agent", "bash"}).list_tools()
    }
    assert "agent" in remaining
    assert "bash" in remaining


def test_profile_is_opt_in_and_gated_on_is_deepseek():
    """The trim must not run without the env opt-in, and never for other
    providers. Pinned against the source gate (the run_print_mode block is
    not callable in isolation)."""
    src = inspect.getsource(headless_mod)
    assert 'getattr(provider, "is_deepseek", False)' in src
    gate = src[src.index("deepseek_core_profile ="):]
    gate = gate[: gate.index("if deepseek_core_profile:")]
    assert '("1", "true", "yes")' in gate, (
        "profile must be opt-in (=1), not opt-out"
    )


def test_headless_arms_sticky_fuse_via_setdefault():
    """Headless (time-budgeted, unattended) arms the sticky escalation the
    provider deliberately defaults off for interactive sessions; setdefault
    so an explicit user value — including 0 — wins. Independent of the tool
    profile."""
    src = inspect.getsource(headless_mod)
    assert 'os.environ.setdefault("CLAWCODEX_DEEPSEEK_FUSE_STICKY", "3")' in src


def test_harbor_adapter_arms_profile_for_deepseek_trials():
    import pathlib

    adapter = pathlib.Path(
        headless_mod.__file__
    ).parents[2] / "eval" / "harbor" / "clawcodex_agent.py"
    src = adapter.read_text()
    assert 'env["CLAWCODEX_DEEPSEEK_CORE_TOOLS"] = "1"' in src


def test_skills_prompt_suppressed_when_profile_trims_skill_tool():
    """The trim removes the Skill tool; the prompt must not advertise
    skills the model cannot invoke (prompt/tool decoupling)."""
    src = inspect.getsource(headless_mod)
    assert "include_skills=not deepseek_core_profile" in src

    from src.query import agent_loop_compat

    sig = inspect.signature(agent_loop_compat.build_effective_system_prompt)
    assert "include_skills" in sig.parameters

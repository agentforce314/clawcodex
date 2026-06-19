"""Tests for the DeepSeek prefix-cache token-efficiency work.

All behaviour is scoped to the ``deepseek`` provider so other providers/models
are provably unaffected. Covers:

* ``BaseProvider.is_deepseek`` capability flag (gate).
* DeepSeek context-window registry rows (and that OpenRouter-DeepSeek and
  other providers keep the default).
* DeepSeek cache-token telemetry in ``_build_usage_dict`` (both wire shapes),
  and that the base/other providers do NOT gain cache keys.
* The system-prompt cache-scope metadata + the query-layer relocation that
  moves per-request-volatile (REQUEST-scope) sections to a trailing tail for
  DeepSeek, keeping the system+history prefix byte-stable even when volatile
  sections (e.g. the mutable MEMORY.md body) change — while leaving every
  other provider's request bytes identical.
"""

from __future__ import annotations

from src.context_system.prompt_assembly import build_full_system_prompt_blocks
from src.models import get_context_window_for_model, get_model_max_output_tokens
from src.providers.base import BaseProvider
from src.providers.deepseek_provider import DeepSeekProvider
from src.providers.openai_provider import OpenAIProvider
from src.query.query import (
    _append_session_context_tail,
    _split_system_prompt_blocks,
    _strip_block_metadata,
)


_KEY = "sk-" + "x" * 24


# --------------------------------------------------------------------------- #
# is_deepseek flag (scope gate)
# --------------------------------------------------------------------------- #

def test_is_deepseek_flag_scoped_to_deepseek_provider():
    assert BaseProvider.is_deepseek is False
    assert DeepSeekProvider(api_key=_KEY).is_deepseek is True
    assert OpenAIProvider(api_key=_KEY).is_deepseek is False


# --------------------------------------------------------------------------- #
# Context-window registry
# --------------------------------------------------------------------------- #

def test_deepseek_v4_context_windows_registered():
    assert get_context_window_for_model("deepseek-v4-pro") == 1_000_000
    assert get_context_window_for_model("deepseek-v4-flash") == 1_000_000
    assert get_model_max_output_tokens("deepseek-v4-pro") == 8_192


def test_other_providers_context_window_unchanged():
    # OpenRouter-DeepSeek (decision #1: out of scope) keeps the 200K default.
    assert get_context_window_for_model("deepseek/deepseek-v4-pro") == 200_000
    assert get_context_window_for_model("gpt-5.4") == 200_000
    assert get_context_window_for_model("some-unknown-model") == 200_000
    # Legacy aliases intentionally NOT registered (broad prefix-match risk).
    assert get_context_window_for_model("deepseek-chat") == 200_000


# --------------------------------------------------------------------------- #
# Cache-token telemetry
# --------------------------------------------------------------------------- #

class _Usage:
    """Minimal stand-in for the OpenAI SDK usage object."""

    def __init__(self, **kw):
        self.prompt_tokens = kw.get("prompt_tokens", 0)
        self.completion_tokens = kw.get("completion_tokens", 0)
        self.total_tokens = kw.get("total_tokens", 0)
        for k, v in kw.items():
            setattr(self, k, v)


def test_deepseek_usage_maps_native_cache_shape_to_anthropic_convention():
    ds = DeepSeekProvider(api_key=_KEY)
    out = ds._build_usage_dict(_Usage(
        prompt_tokens=1000, completion_tokens=50, total_tokens=1050,
        prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=200,
    ))
    # input_tokens = uncached (miss); cache_read = cached (hit); no cache-write.
    assert out["input_tokens"] == 200
    assert out["cache_read_input_tokens"] == 800
    assert out["cache_creation_input_tokens"] == 0
    assert out["output_tokens"] == 50
    assert out["total_tokens"] == 1050


def test_deepseek_usage_nested_openai_cache_shape_derives_miss():
    ds = DeepSeekProvider(api_key=_KEY)

    class _Details:
        cached_tokens = 700

    usage = _Usage(prompt_tokens=1000, completion_tokens=10, total_tokens=1010)
    usage.prompt_tokens_details = _Details()
    out = ds._build_usage_dict(usage)
    assert out["cache_read_input_tokens"] == 700
    assert out["input_tokens"] == 300  # derived: prompt - hit


def test_deepseek_usage_model_extra_dict():
    ds = DeepSeekProvider(api_key=_KEY)
    usage = _Usage(prompt_tokens=500, completion_tokens=5, total_tokens=505)
    usage.model_extra = {"prompt_cache_hit_tokens": 400}
    out = ds._build_usage_dict(usage)
    assert out["cache_read_input_tokens"] == 400
    assert out["input_tokens"] == 100


def test_deepseek_usage_no_cache_hit_leaves_input_full():
    """No cache hit → no re-map: all prompt tokens stay as uncached input."""
    ds = DeepSeekProvider(api_key=_KEY)
    out = ds._build_usage_dict(
        _Usage(prompt_tokens=300, completion_tokens=20, total_tokens=320)
    )
    assert out["input_tokens"] == 300
    assert "cache_read_input_tokens" not in out


def test_deepseek_usage_reads_reasoning_tokens():
    ds = DeepSeekProvider(api_key=_KEY)

    class _CDetails:
        reasoning_tokens = 42

    usage = _Usage(prompt_tokens=10, completion_tokens=100, total_tokens=110)
    usage.completion_tokens_details = _CDetails()
    assert ds._build_usage_dict(usage)["reasoning_tokens"] == 42


def test_deepseek_usage_none_is_safe():
    assert DeepSeekProvider(api_key=_KEY)._build_usage_dict(None) == {}


def test_other_provider_usage_unchanged_by_cache_fields():
    """Isolation: the base OpenAI-compatible usage dict is untouched even if a
    usage object carries DeepSeek-style cache fields."""
    out = OpenAIProvider(api_key=_KEY)._build_usage_dict(_Usage(
        prompt_tokens=1000, completion_tokens=50, total_tokens=1050,
        prompt_cache_hit_tokens=800,
    ))
    assert "cache_read_input_tokens" not in out
    assert out == {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050}


# --------------------------------------------------------------------------- #
# Cost wiring (services/pricing.py)
# --------------------------------------------------------------------------- #

def test_deepseek_pricing_registered():
    from src.services.pricing import get_pricing

    flash = get_pricing("deepseek-v4-flash")
    pro = get_pricing("deepseek-v4-pro")
    assert flash is not None and pro is not None
    assert flash["input"] == 0.14 / 1_000_000
    assert flash["cache_read"] == 0.0028 / 1_000_000
    assert pro["input"] == 0.435 / 1_000_000
    assert pro["output"] == 0.87 / 1_000_000


def test_openrouter_deepseek_pricing_via_vendor_strip():
    """Consistent with how all proxied models are priced at the upstream rate
    (get_pricing strips the ``deepseek/`` vendor prefix)."""
    from src.services.pricing import get_pricing

    assert get_pricing("deepseek/deepseek-v4-pro") == get_pricing("deepseek-v4-pro")


def test_deepseek_cost_credits_cache_hit_end_to_end():
    """A DeepSeek response mapped through the provider then priced charges the
    cheap cache-hit rate for the cached portion — the whole point of the
    prefix-cache work."""
    from src.services.pricing import compute_cost

    ds = DeepSeekProvider(api_key=_KEY)
    usage = ds._build_usage_dict(_Usage(
        prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000,
        prompt_cache_hit_tokens=900_000, prompt_cache_miss_tokens=100_000,
    ))
    cost = compute_cost("deepseek-v4-flash", usage)
    expected = 100_000 * 0.14 / 1_000_000 + 900_000 * 0.0028 / 1_000_000
    assert abs(cost - expected) < 1e-12
    # ~9x cheaper than pricing the whole prompt as uncached input.
    full = 1_000_000 * 0.14 / 1_000_000
    assert cost < full / 5


def test_cost_command_surfaces_cache_hit_rate():
    """End-to-end: a recorded DeepSeek usage appears in /cost with the
    prompt-cache hit-rate and a cache-credited cost."""
    from pathlib import Path

    from src.bootstrap.state import reset_cost_state
    from src.command_system.builtins import cost_command_call
    from src.command_system.types import CommandContext
    from src.cost_tracker import record_api_usage

    reset_cost_state()
    try:
        ds = DeepSeekProvider(api_key=_KEY)
        usage = ds._build_usage_dict(_Usage(
            prompt_tokens=100_000, completion_tokens=500, total_tokens=100_500,
            prompt_cache_hit_tokens=90_000, prompt_cache_miss_tokens=10_000,
        ))
        record_api_usage("deepseek-v4-pro", usage)
        ctx = CommandContext(
            workspace_root=Path("/tmp"), cwd=Path("/tmp"),
            conversation=None, cost_tracker=None, history=None,
        )
        out = cost_command_call("", ctx).value
        assert "deepseek-v4-pro" in out
        assert "90,000 cached" in out
        assert "90% hit" in out
    finally:
        reset_cost_state()


# --------------------------------------------------------------------------- #
# cache_scope metadata tagging (assembler)
# --------------------------------------------------------------------------- #

def _scope_of(blocks, needle):
    for b in blocks:
        if needle in b.get("text", ""):
            return b.get("_cache_scope")
    return None


def test_assembler_tags_blocks_with_cache_scope():
    blocks = build_full_system_prompt_blocks(cwd="/tmp")
    # The env section is per-request volatile → REQUEST scope.
    assert _scope_of(blocks, "# Environment") == "request"
    # The intro/identity is globally cacheable → GLOBAL scope.
    assert _scope_of(blocks, "software engineering tasks") == "global"
    # Every text block except the boundary marker carries a scope tag.
    from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
    for b in blocks:
        if b.get("text") == SYSTEM_PROMPT_DYNAMIC_BOUNDARY:
            assert "_cache_scope" not in b
        else:
            assert b.get("_cache_scope") in {"global", "session", "request"}


# --------------------------------------------------------------------------- #
# _strip_block_metadata (Anthropic wire safety)
# --------------------------------------------------------------------------- #

def test_strip_block_metadata_removes_cache_scope_only():
    blocks = [
        {"type": "text", "text": "a", "_cache_scope": "global",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "b"},
    ]
    cleaned = _strip_block_metadata(blocks)
    assert all("_cache_scope" not in b for b in cleaned)
    # cache_control and other keys are preserved.
    assert cleaned[0]["cache_control"] == {"type": "ephemeral"}
    assert cleaned[0]["text"] == "a"
    # Original is not mutated.
    assert blocks[0]["_cache_scope"] == "global"


# --------------------------------------------------------------------------- #
# _split_system_prompt_blocks (the relocation core)
# --------------------------------------------------------------------------- #

def _sample_blocks():
    from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
    return [
        {"type": "text", "text": "INTRO", "_cache_scope": "global"},
        {"type": "text", "text": SYSTEM_PROMPT_DYNAMIC_BOUNDARY},
        {"type": "text", "text": "TOOLS", "_cache_scope": "session"},
        {"type": "text", "text": "ENV-and-MEMORY", "_cache_scope": "request"},
    ]


def test_split_relocates_request_scope_for_deepseek():
    system, tail = _split_system_prompt_blocks(_sample_blocks(), relocate_request_scope=True)
    # Stable prefix keeps GLOBAL + SESSION, drops the boundary marker.
    assert "INTRO" in system and "TOOLS" in system
    assert "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__" not in system
    # REQUEST-scope content is moved out of the prefix into the tail.
    assert "ENV-and-MEMORY" not in system
    assert tail == "ENV-and-MEMORY"


def test_split_keeps_everything_in_system_for_other_providers():
    system, tail = _split_system_prompt_blocks(_sample_blocks(), relocate_request_scope=False)
    assert "INTRO" in system and "TOOLS" in system and "ENV-and-MEMORY" in system
    assert "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__" not in system
    assert tail == ""  # no relocation → no tail for non-DeepSeek


def test_deepseek_prefix_stable_when_request_scope_block_changes():
    """The core guarantee: a mid-session change to a REQUEST-scope section
    (e.g. a MEMORY.md write, or the live env timestamp) must NOT perturb the
    DeepSeek system prefix — only the relocated tail changes."""
    from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
    turn1 = [
        {"type": "text", "text": "INTRO", "_cache_scope": "global"},
        {"type": "text", "text": SYSTEM_PROMPT_DYNAMIC_BOUNDARY},
        {"type": "text", "text": "TOOLS", "_cache_scope": "session"},
        {"type": "text", "text": "MEMORY v1", "_cache_scope": "request"},
    ]
    turn2 = [
        {"type": "text", "text": "INTRO", "_cache_scope": "global"},
        {"type": "text", "text": SYSTEM_PROMPT_DYNAMIC_BOUNDARY},
        {"type": "text", "text": "TOOLS", "_cache_scope": "session"},
        {"type": "text", "text": "MEMORY v2 (user added a note)", "_cache_scope": "request"},
    ]
    sys1, tail1 = _split_system_prompt_blocks(turn1, relocate_request_scope=True)
    sys2, tail2 = _split_system_prompt_blocks(turn2, relocate_request_scope=True)
    assert sys1 == sys2, "DeepSeek system prefix must be stable across a memory write"
    assert tail1 != tail2, "the changed content rides the (uncached) tail"


def test_non_deepseek_prefix_changes_when_request_scope_block_changes():
    """Contrast: without relocation, the same change lands in the system
    prefix (this is the current behaviour for non-DeepSeek providers — they
    rely on Anthropic-style explicit cache_control, not byte-stability)."""
    base = {"type": "text", "text": "INTRO", "_cache_scope": "global"}
    sys1, _ = _split_system_prompt_blocks(
        [base, {"type": "text", "text": "MEM v1", "_cache_scope": "request"}],
        relocate_request_scope=False,
    )
    sys2, _ = _split_system_prompt_blocks(
        [base, {"type": "text", "text": "MEM v2", "_cache_scope": "request"}],
        relocate_request_scope=False,
    )
    assert sys1 != sys2


def test_memory_section_is_request_scoped():
    """Regression guard tying the relocation guarantee to the real section
    taxonomy: the auto-memory section embeds the mutable MEMORY.md body, so it
    MUST stay REQUEST-scoped (relocated to the tail for DeepSeek). If it's ever
    retagged SESSION/GLOBAL it would sit in the cached prefix and a memory
    write would bust the whole history cache. Skips when no memory section is
    produced in the test environment."""
    from src.context_system.prompt_assembly import _build_memory_section
    from src.context_system.system_prompt_cache import CacheScope

    section = _build_memory_section()
    if section is not None:
        assert section.cache_scope is CacheScope.REQUEST


# --------------------------------------------------------------------------- #
# _append_session_context_tail (placement / alternation hardening)
# --------------------------------------------------------------------------- #

def test_tail_merges_into_trailing_string_user_turn():
    """Fresh turn ending in a string user prompt: merge the tail into it so the
    wire keeps strict user/assistant alternation (no consecutive user msgs)."""
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "hello"}]
    out = _append_session_context_tail(msgs, "ENV+MEM")
    assert len(out) == len(msgs)  # merged, not appended
    assert out[-1]["role"] == "user"
    assert out[-1]["content"].startswith("hello")
    assert "ENV+MEM" in out[-1]["content"]
    # input not mutated
    assert msgs[-1]["content"] == "hello"


def test_tail_is_standalone_message_after_tool_result():
    """Mid-tool-loop: the turn ends in a tool_result (→ role:tool on the wire),
    so the tail must be a NEW trailing user message to land after it."""
    msgs = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]
    out = _append_session_context_tail(msgs, "ENV+MEM")
    assert len(out) == len(msgs) + 1
    assert out[-1]["role"] == "user"
    assert "ENV+MEM" in out[-1]["content"]
    # the tool_result message is untouched (tail did not merge into it)
    assert out[-2]["content"][0]["type"] == "tool_result"


def test_tail_merges_into_multimodal_user_turn_without_tool_result():
    """A fresh user turn with image/text blocks (no tool_result) merges the
    tail as an extra text block, preserving a single user turn."""
    msgs = [{"role": "user", "content": [{"type": "text", "text": "look"}]}]
    out = _append_session_context_tail(msgs, "ENV")
    assert len(out) == 1
    assert out[-1]["content"][-1]["type"] == "text"
    assert "ENV" in out[-1]["content"][-1]["text"]

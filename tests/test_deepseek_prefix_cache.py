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
    # DeepSeek's documented ceiling. Was 8_192 — a placeholder that
    # under-reported the model 47x in the token warning and looked absurd
    # beside a 1M context window.
    #
    # Advisory on this wire: OpenAICompatibleProvider sends no ``max_tokens``
    # field (unlike AnthropicProvider's _default_max_tokens or Gemini's
    # config_kwargs), so DeepSeek applies its own server default either way,
    # and the auto-compact reservation clamps to MAX_OUTPUT_TOKENS_FOR_SUMMARY
    # (20_000). Effective input therefore moves 991_808 -> 980_000 only.
    # Pinned so nobody "fixes" a timeout by editing this number: it cannot
    # truncate a response, because it never reaches the request.
    assert get_model_max_output_tokens("deepseek-v4-pro") == 384_000
    assert get_model_max_output_tokens("deepseek-v4-flash") == 384_000


def test_other_providers_context_window_unchanged():
    # OpenRouter-DeepSeek (decision #1: out of scope) keeps the 200K default.
    assert get_context_window_for_model("deepseek/deepseek-v4-pro") == 200_000
    # gpt-5.4 gained a registered 272K window with the ChatGPT-subscription
    # work (models/configs.py) — no longer the 200K unknown-model default.
    assert get_context_window_for_model("gpt-5.4") == 272_000
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


def test_memory_index_is_request_scoped_and_doctrine_is_not():
    """Regression guard tying the relocation guarantee to the real section
    taxonomy.

    Two halves, two scopes, and the split is the whole point:

    * ``memory_index`` embeds the mutable ``MEMORY.md`` body, so it MUST stay
      REQUEST-scoped (relocated to the tail for DeepSeek). If it were ever
      retagged SESSION/GLOBAL it would sit in the cached prefix and a memory
      write would bust the whole history cache.
    * ``memory`` is the typed-memory doctrine, which interpolates nothing that
      changes within a session. It MUST NOT be REQUEST-scoped: the tail is
      re-sent and re-billed as a cache miss on every single request, and this
      block is ~3.4K tokens. Tagging it REQUEST cost ~3.4K miss tokens per turn
      on terminal-bench 2.1 — the largest single contributor to the 90.2% vs
      98.2% cache-hit gap against Reasonix.

    Skips whichever half the test environment does not produce.
    """
    from src.context_system.prompt_assembly import _build_memory_sections
    from src.context_system.system_prompt_cache import CacheScope

    sections = {s.id: s for s in _build_memory_sections()}

    index = sections.get("memory_index")
    if index is not None:
        assert index.cache_scope is CacheScope.REQUEST
        assert "MEMORY.md" in index.content

    doctrine = sections.get("memory")
    if doctrine is not None:
        assert doctrine.cache_scope is not CacheScope.REQUEST
        # The mutable body must not have leaked back into the cached half.
        assert "## MEMORY.md" not in doctrine.content


def test_relocated_tail_stays_within_a_token_budget(tmp_path, monkeypatch):
    """Budget guard on the whole REQUEST-scope group, not just the memory half.

    Everything tagged REQUEST is relocated behind the conversation for
    DeepSeek, which means it is re-sent on every request and re-billed at the
    cache-miss rate — the prefix cache can never cover it. So the tail's size
    is a direct, permanent per-turn tax, and the only things that earn a place
    there are values that actually change between turns.

    The number below is deliberately generous (~500 tokens against a real tail
    of roughly 200). It is a tripwire for a whole *section* being misfiled, not
    a style rule: the auto-memory doctrine alone was 13.5KB in this group and
    cost ~3.4K miss tokens on every single request. If this fails, do not raise
    the budget — split the offending section and leave only its volatile part
    behind, the way ``build_memory_prompt_parts`` and
    ``build_context_prompt_parts`` do.

    MEMORY.md is pointed at an empty temp dir so a developer's real (and
    legitimately large) memory index cannot make this flaky.
    """
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(tmp_path))

    blocks = build_full_system_prompt_blocks(cwd=str(tmp_path), non_interactive=True)
    request_blocks = [b for b in blocks if b.get("_cache_scope") == "request"]
    total = sum(len(b.get("text", "")) for b in request_blocks)

    detail = "\n".join(
        f"  {len(b.get('text', '')):6d} ch | "
        f"{(b.get('text', '').strip().splitlines() or [''])[0][:70]}"
        for b in request_blocks
    )
    assert total < 2000, (
        f"REQUEST-scope group is {total} chars (~{total // 4} tokens); every one "
        f"of those is re-sent and re-billed as a cache miss on every DeepSeek "
        f"request. Blocks:\n{detail}"
    )


def test_relocation_conserves_content_exactly(tmp_path, monkeypatch):
    """Relocation must move bytes, never drop or duplicate them.

    This is the capability guarantee behind the cache work: the model has to
    see exactly the same content, just partitioned so the stable part can be
    cached. Concatenating the DeepSeek ``system + tail`` must therefore yield
    the same character count as the single flattened system prompt every other
    provider receives — anything else means a section went missing (the model
    is now blind to it) or got emitted twice (wasted tokens and a confusing
    double instruction).
    """
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(tmp_path))
    (tmp_path / "MEMORY.md").write_text("- [Thing](thing.md) — hook\n")

    blocks = build_full_system_prompt_blocks(cwd=str(tmp_path), non_interactive=True)
    flat, empty_tail = _split_system_prompt_blocks(blocks, relocate_request_scope=False)
    system, tail = _split_system_prompt_blocks(blocks, relocate_request_scope=True)

    assert empty_tail == "", "non-relocating providers must get no tail"
    # Joining introduces separator whitespace, so compare on non-whitespace
    # characters: that is invariant under how the two halves are glued.
    def _dense(s):
        return "".join(s.split())

    assert _dense(system) + _dense(tail) == _dense(flat), (
        "DeepSeek's system+tail must carry exactly the flattened prompt's "
        "content — no section dropped, none duplicated"
    )
    assert tail, "something volatile should still be relocated"


def test_prefix_survives_a_mid_session_memory_write(tmp_path, monkeypatch):
    """The split must not cost the freshness guarantee it was carved out of.

    Splitting doctrine into the cached prefix is only safe if the half that
    actually changes stays behind. The model rewrites ``MEMORY.md`` mid-session
    via the Memory tool; when it does, the DeepSeek system prefix must be
    untouched (only the relocated tail moves), exactly as before the split.
    Getting this wrong would trade a per-turn tax for something far worse — a
    full prefix bust every time the agent saved a memory.
    """
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(tmp_path))
    entrypoint = tmp_path / "MEMORY.md"

    entrypoint.write_text("- [First](first.md) — hook\n")
    sys1, tail1 = _split_system_prompt_blocks(
        build_full_system_prompt_blocks(cwd=str(tmp_path), non_interactive=True),
        relocate_request_scope=True,
    )
    entrypoint.write_text("- [First](first.md) — hook\n- [Second](second.md) — hook\n")
    sys2, tail2 = _split_system_prompt_blocks(
        build_full_system_prompt_blocks(cwd=str(tmp_path), non_interactive=True),
        relocate_request_scope=True,
    )

    assert sys1 == sys2, "a MEMORY.md write must not perturb the DeepSeek prefix"
    assert tail1 != tail2, "the new memory must actually reach the model"
    assert "Second" in tail2 and "Second" not in sys2


def test_memory_sections_rejoin_to_the_legacy_single_section():
    """The split must be a pure relocation, not a prompt edit.

    Providers that do not relocate REQUEST scope concatenate every section, so
    doctrine + index have to rejoin into exactly the bytes the old single
    ``memory`` section produced. ``build_full_system_prompt`` is covered
    end-to-end elsewhere; this pins the memdir-level contract directly.
    """
    import tempfile
    from pathlib import Path

    from src.memdir import build_memory_prompt, build_memory_prompt_parts

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "MEMORY.md").write_text("- [Thing](thing.md) — hook\n")
        whole = build_memory_prompt(display_name="auto memory", memory_dir=tmp)
        guidance, index = build_memory_prompt_parts(
            display_name="auto memory", memory_dir=tmp
        )
        assert f"{guidance}\n{index}" == whole
        # And the halves are actually split on the intended seam.
        assert "## MEMORY.md" not in guidance
        assert index.startswith("## MEMORY.md")


# --------------------------------------------------------------------------- #
# build_effective_system_prompt trailing context block (new-TUI cutover path)
#
# The live TUI + headless route through build_effective_system_prompt, which
# appends a build_context_prompt block — a *live workspace snapshot* embedding
# ``git status`` (+ file counts / top-level entries). Those mutate the moment the
# agent edits a file, i.e. essentially every turn. The block MUST be REQUEST-
# scoped so DeepSeek relocates it out of the byte-stable system prefix; otherwise
# a single mid-session file edit busts the whole prefix cache. (Regression for
# the gap where the block was appended untagged.)
# --------------------------------------------------------------------------- #

_CTX_HEAD = "## Runtime Context\n- Today's date: 2026-07-01\n- Workspace root: /repo\n\n"
_CTX_CLAUDE = "\n\n## Project Instructions\nCLAUDE_MD_SENTINEL\n"
# Turn 1 vs Turn 2: same session, but the agent created a file so git status changed.
_CTX_GIT_T1 = _CTX_HEAD + "## Git Context\nCurrent branch: main\n\nStatus:\n M src/foo.py" + _CTX_CLAUDE
_CTX_GIT_T2 = _CTX_HEAD + "## Git Context\nCurrent branch: main\n\nStatus:\n M src/foo.py\n?? src/new.py" + _CTX_CLAUDE


def _effective_blocks(context_text, tmp_path):
    """build_effective_system_prompt with the context builder stubbed to a fixed
    workspace snapshot, so the test pins the trailing-block scope deterministically.

    ``context_text`` is the whole legacy blob; it is split on the
    ``## Project Instructions`` seam to feed ``build_context_prompt_parts``,
    which is what the builder now calls.
    """
    from unittest import mock

    from src.query.agent_loop_compat import build_effective_system_prompt
    from src.tool_system.context import ToolContext

    head, sep, tail_txt = context_text.partition("## Project Instructions")
    parts = (head.strip("\n"), (sep + tail_txt).strip("\n") if sep else "")

    ctx = ToolContext(workspace_root=tmp_path)
    with mock.patch(
        "src.context_system.build_context_prompt_parts", return_value=parts
    ):
        return build_effective_system_prompt(
            "", ctx, provider=DeepSeekProvider(api_key=_KEY)
        )


def test_effective_prompt_tags_context_block_request_scope(tmp_path):
    blocks = _effective_blocks(_CTX_GIT_T1, tmp_path)
    ctx_block = next(b for b in blocks if "## Git Context" in b.get("text", ""))
    assert ctx_block.get("_cache_scope") == "request"


def test_effective_prompt_keeps_project_instructions_out_of_request_scope(tmp_path):
    """CLAWCODEX.md is read once and fixed for the session, so it must NOT ride
    the relocated tail — everything there is re-sent and re-billed as a cache
    miss on every request. Only the live workspace/git snapshot belongs there."""
    blocks = _effective_blocks(_CTX_GIT_T1, tmp_path)
    instr = next(b for b in blocks if "CLAUDE_MD_SENTINEL" in b.get("text", ""))
    assert instr.get("_cache_scope") != "request"
    # ...and it must not have been merged back into the volatile block.
    snapshot = next(b for b in blocks if "## Git Context" in b.get("text", ""))
    assert "CLAUDE_MD_SENTINEL" not in snapshot.get("text", "")


def test_deepseek_relocates_git_context_but_caches_project_instructions(tmp_path):
    """The volatile workspace snapshot rides the tail; CLAWCODEX.md stays in the
    cached prefix.

    The prefix placement is the point: CLAWCODEX.md can be many KB and never
    changes mid-session, so paying for it in the tail on every turn was pure
    waste. It must still be present somewhere — dropping it would lose the
    user's project instructions entirely.
    """
    blocks = _effective_blocks(_CTX_GIT_T1, tmp_path)
    system, tail = _split_system_prompt_blocks(blocks, relocate_request_scope=True)
    assert "## Git Context" not in system and "Status:" not in system
    assert "## Git Context" in tail
    assert "CLAUDE_MD_SENTINEL" in system
    assert "CLAUDE_MD_SENTINEL" not in tail


def test_deepseek_prefix_stable_across_mid_session_git_change(tmp_path):
    """Core guarantee for the new-TUI path: a file edit that changes ``git
    status`` must NOT perturb the DeepSeek system prefix — only the tail."""
    sys1, tail1 = _split_system_prompt_blocks(
        _effective_blocks(_CTX_GIT_T1, tmp_path), relocate_request_scope=True
    )
    sys2, tail2 = _split_system_prompt_blocks(
        _effective_blocks(_CTX_GIT_T2, tmp_path), relocate_request_scope=True
    )
    assert sys1 == sys2, "a git-status change must not bust the DeepSeek prefix"
    assert tail1 != tail2, "the changed snapshot rides the (uncached) tail"


def test_non_deepseek_keeps_context_block_in_system(tmp_path):
    """No regression: for non-DeepSeek providers the context block stays in the
    flattened system string (byte-for-byte the prior behaviour), tail empty."""
    system, tail = _split_system_prompt_blocks(
        _effective_blocks(_CTX_GIT_T1, tmp_path), relocate_request_scope=False
    )
    assert "## Git Context" in system and "CLAUDE_MD_SENTINEL" in system
    assert tail == ""


def test_deepseek_end_to_end_relocates_context_after_history(tmp_path):
    """End-to-end through the exact new-TUI adapter the agent-server uses:
    build_effective_system_prompt -> run_query_as_agent_loop -> _call_model_sync
    (DeepSeek). Pins the full wire composition: the live workspace/git/CLAWCODEX.md
    context must be ABSENT from the system message and instead ride a trailing
    user <system-reminder> that lands AFTER the conversation history."""
    import asyncio
    from unittest import mock

    from src.providers.base import ChatResponse
    from src.query.agent_loop_compat import (
        build_effective_system_prompt,
        run_query_as_agent_loop,
    )
    from src.tool_system.context import ToolContext
    from src.tool_system.defaults import build_default_registry
    from src.types.messages import UserMessage

    captured: list = []

    class _CapturingDeepSeek(DeepSeekProvider):
        def chat_stream_response(self, messages, tools=None, on_text_chunk=None,
                                 abort_signal=None, on_thinking_chunk=None, **kwargs):
            captured.append(messages)
            return ChatResponse(
                content="ok", model=self.model,
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="end_turn", tool_uses=None,
            )

    provider = _CapturingDeepSeek(api_key=_KEY)
    ctx = ToolContext(workspace_root=tmp_path)
    registry = build_default_registry(provider=provider)

    head, sep, rest = _CTX_GIT_T1.partition("## Project Instructions")
    with mock.patch(
        "src.context_system.build_context_prompt_parts",
        return_value=(head.strip("\n"), (sep + rest).strip("\n")),
    ):
        system_prompt = build_effective_system_prompt("", ctx, provider=provider)

    asyncio.run(run_query_as_agent_loop(
        initial_messages=[UserMessage(content="hello there")],
        provider=provider,
        tool_registry=registry,
        tool_context=ctx,
        system_prompt=system_prompt,
        max_turns=1,
        on_text_chunk=lambda _s: None,
    ))

    assert captured, "the DeepSeek provider was never called"
    wire = captured[0]
    # System message (index 0) is free of the volatile snapshot, but DOES carry
    # the session-stable CLAWCODEX.md so it is paid for once and then cached.
    assert wire[0]["role"] == "system"
    assert "## Git Context" not in wire[0]["content"]
    assert "CLAUDE_MD_SENTINEL" in wire[0]["content"]
    # The relocated snapshot rides the LAST message (after the history) as a
    # user <system-reminder>.
    last = wire[-1]
    last_text = (
        last["content"] if isinstance(last["content"], str) else str(last["content"])
    )
    assert last["role"] == "user"
    assert "<system-reminder>" in last_text
    assert "## Git Context" in last_text
    assert "CLAUDE_MD_SENTINEL" not in last_text


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

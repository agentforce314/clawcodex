"""Cross-provider follow-ups: cache-read accounting, reasoning field names,
foreign-block stripping, and the README provider list.

Each of these was a silent gap — nothing errored, the numbers and the
transcript were just quietly wrong — so the tests assert the observable
consequence rather than the shape of the code.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from src.providers import PROVIDER_INFO, get_provider_class
from src.providers.deepseek_provider import DeepSeekProvider
from src.providers.openai_compatible import _extract_reasoning
from src.services.pricing import compute_cost


def _usage(prompt: int, cached: int | None = None, *, as_dict: bool = False):
    details = None
    if cached is not None:
        details = {"cached_tokens": cached} if as_dict else type(
            "D", (), {"cached_tokens": cached}
        )()
    return type(
        "U",
        (),
        {
            "prompt_tokens": prompt,
            "completion_tokens": 50,
            "total_tokens": prompt + 50,
            "prompt_tokens_details": details,
            "completion_tokens_details": None,
        },
    )()


# --- cache-read accounting ------------------------------------------------


def test_cached_prompt_tokens_are_split_out_of_billable_input() -> None:
    """`prompt_tokens` INCLUDES cache hits, so reporting it whole over-bills.

    Every pricing tier carries a `cache_read` rate, and two of them say in
    comments that the rate is inert until this mapping lands.
    """
    provider = get_provider_class("groq")(api_key="sk-test")
    usage = provider._build_usage_dict(_usage(1000, cached=900))
    assert usage["input_tokens"] == 100
    assert usage["cache_read_input_tokens"] == 900
    assert usage["cache_creation_input_tokens"] == 0
    # the full prompt is still recoverable
    assert usage["input_tokens"] + usage["cache_read_input_tokens"] == 1000


def test_the_cache_split_actually_lowers_the_reported_cost() -> None:
    """The point of the split is money, so assert money.

    Numbers are a real turn measured against DeepSeek (2613-token prompt,
    2560 of it served from the prefix cache).
    """
    mapped = {
        "input_tokens": 53,
        "output_tokens": 8,
        "cache_read_input_tokens": 2560,
        "cache_creation_input_tokens": 0,
    }
    unmapped = {"input_tokens": 2613, "output_tokens": 8}
    for model in ("deepseek-v4-pro", "openai/gpt-5.6-luna"):
        cheap = compute_cost(model, mapped)
        dear = compute_cost(model, unmapped)
        assert cheap > 0, model
        assert dear > cheap * 2, f"{model}: cache split had no material effect"


def test_providers_that_report_no_cache_are_unchanged() -> None:
    """No cache hit must produce byte-identical output to before the change."""
    provider = get_provider_class("groq")(api_key="sk-test")
    for usage in (_usage(1000), _usage(1000, cached=0)):
        assert provider._build_usage_dict(usage) == {
            "input_tokens": 1000,
            "output_tokens": 50,
            "total_tokens": 1050,
        }


def test_details_are_read_whether_object_or_mapping() -> None:
    """Gateways differ on the shape; both must map."""
    provider = get_provider_class("groq")(api_key="sk-test")
    as_obj = provider._build_usage_dict(_usage(100, cached=40))
    as_map = provider._build_usage_dict(_usage(100, cached=40, as_dict=True))
    assert as_obj == as_map
    assert as_obj["cache_read_input_tokens"] == 40


def test_a_cache_count_larger_than_the_prompt_cannot_go_negative() -> None:
    provider = get_provider_class("groq")(api_key="sk-test")
    usage = provider._build_usage_dict(_usage(10, cached=999))
    assert usage["input_tokens"] == 0


@pytest.mark.parametrize(
    "native,nested",
    [
        ({"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 100}, None),
        (None, 900),
        ({"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 100}, 900),
    ],
)
def test_deepseek_agrees_with_the_base_on_every_usage_shape(native, nested) -> None:
    """DeepSeek's override runs AFTER the base now performs the same split.

    Without compensating, its `prompt_tokens - hit` subtraction ran twice and
    drove billable input to zero — under-reporting cost on the provider whose
    prefix cache makes this fire on nearly every turn.
    """
    attrs = {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "total_tokens": 1050,
        "completion_tokens_details": None,
        "prompt_tokens_details": type("D", (), {"cached_tokens": nested})()
        if nested
        else None,
    }
    if native:
        attrs.update(native)
    usage = DeepSeekProvider(api_key="sk-test")._build_usage_dict(
        type("U", (), attrs)()
    )
    assert usage["input_tokens"] == 100
    assert usage["cache_read_input_tokens"] == 900


# --- reasoning field names ------------------------------------------------


def test_reasoning_is_read_under_either_provider_field_name() -> None:
    """OpenRouter streams `reasoning`; DeepSeek/GLM stream `reasoning_content`.

    Verified live 2026-08-02: a streamed gpt-5.6-luna turn over OpenRouter
    carried `delta.reasoning` and `delta.reasoning_details` and NO
    `reasoning_content` key at all, so reading only the latter dropped every
    reasoning token from the provider this repo's benchmarks run on.
    """
    make = lambda **kw: type("X", (), kw)()  # noqa: E731
    assert _extract_reasoning(make(reasoning_content="rc")) == "rc"
    assert _extract_reasoning(make(reasoning="r")) == "r"
    # the established name wins when a provider somehow sends both
    assert _extract_reasoning(make(reasoning_content="rc", reasoning="r")) == "rc"


def test_absent_empty_and_non_string_reasoning_are_ignored() -> None:
    """OpenRouter sends `reasoning: None` when the trace is encrypted."""
    make = lambda **kw: type("X", (), kw)()  # noqa: E731
    assert _extract_reasoning(make(content="hi")) is None
    assert _extract_reasoning(make(reasoning="")) is None
    assert _extract_reasoning(make(reasoning=None)) is None
    assert _extract_reasoning(make(reasoning=["structured"])) is None


# --- foreign-block stripping ---------------------------------------------


def test_gemini_drops_responses_item_blocks_from_history() -> None:
    """Gemini strips ChatGPT replay blocks explicitly, like the other three.

    This pins consistency, not a live bug: the converter's `if parts:` guard
    already drops a message whose blocks all fell through the if/elif chain,
    so today the observable result is identical either way. What the strip
    buys is that the outcome stops depending on two implicit fallthroughs —
    adding an `else` branch or a placeholder part would otherwise start
    forwarding replay items to Gemini silently.

    Because the behaviours coincide, the assertion below is on the shared
    helper's contract plus the converter's output shape; removing the strip
    call does NOT fail this test, and that is expected rather than a gap.
    """
    from src.providers.openai_responses import strip_responses_item_blocks

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "openai_responses_item", "item": {}}]},
        {
            "role": "assistant",
            "content": [
                {"type": "openai_responses_item", "item": {}},
                {"type": "text", "text": "kept"},
            ],
        },
    ]
    stripped = strip_responses_item_blocks(history)
    assert len(stripped) == 2, "reasoning-only turn should be dropped entirely"
    assert stripped[-1]["content"] == [{"type": "text", "text": "kept"}]

    # Driven through the converter, not grepped for. A source check would
    # pass on any mention of the helper, including an unreached one.
    import sys
    from unittest.mock import MagicMock

    sys.modules.setdefault("google", MagicMock())
    sys.modules.setdefault("google.genai", MagicMock())
    import src.providers.gemini_provider as gemini

    with patch.object(gemini, "_ensure_sdk", lambda: None), patch.object(
        gemini, "genai_types", MagicMock()
    ) as fake_types:
        fake_types.Part.from_text = lambda text: {"text": text}
        seen: list[list] = []
        fake_types.Content = lambda role, parts: seen.append(parts) or {"parts": parts}
        provider = gemini.GeminiProvider.__new__(gemini.GeminiProvider)
        contents, _ = provider._convert_messages(history)

    assert len(contents) == 2, (
        "the reasoning-only assistant turn should be gone, not present-but-empty"
    )
    assert all(parts for parts in seen), "no message may convert to empty parts"


# --- README ---------------------------------------------------------------


def test_the_readme_provider_list_matches_the_registry() -> None:
    """The list drifted by five providers before this was pinned."""
    readme = Path("README.md").read_text(encoding="utf-8")
    block = re.search(r"providers = \[(.*?)\]  # (\d+) providers", readme, re.S)
    assert block, "provider list block not found in README.md"

    listed = set(re.findall(r'"([a-z0-9\-]+)"', block.group(1)))
    assert listed == set(PROVIDER_INFO), (
        f"README missing {sorted(set(PROVIDER_INFO) - listed)}, "
        f"extra {sorted(listed - set(PROVIDER_INFO))}"
    )
    assert int(block.group(2)) == len(PROVIDER_INFO)


def test_context_accounting_still_sees_the_whole_prompt() -> None:
    """Splitting input must not shrink what the context display counts.

    `input_tokens` no longer means "the whole prompt", so anything treating
    it that way would under-count context and delay auto-compaction until
    after the real limit. The consumers sum all three parts
    (`context_analyzer`, `tasks/progress`, `cost_tracker`), which is the
    Anthropic convention the codebase was already built around — this pins
    that the parts still reconstruct the total.
    """
    provider = get_provider_class("groq")(api_key="sk-test")
    usage = provider._build_usage_dict(_usage(2613, cached=2560))

    context_total = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    assert context_total == 2613


def test_the_cost_tracker_records_the_cache_split_rather_than_flattening_it() -> None:
    from src.bootstrap import state as bootstrap_state

    provider = get_provider_class("groq")(api_key="sk-test")
    usage = provider._build_usage_dict(_usage(2613, cached=2560))

    assert hasattr(bootstrap_state.ModelUsage(), "cache_read_input_tokens")
    recorded = bootstrap_state.ModelUsage(
        input_tokens=usage["input_tokens"],
        cache_read_input_tokens=usage["cache_read_input_tokens"],
    )
    assert recorded.input_tokens == 53
    assert recorded.cache_read_input_tokens == 2560


# --- the reasoning CALL SITES, not just the helper ------------------------


def _openrouter_message(text: str, reasoning: str | None):
    """A Chat Completions message shaped the way OpenRouter sends one.

    A plain object, not a MagicMock: a mock auto-creates
    ``reasoning_content``, which would let a call site reading only that name
    pass.
    """
    msg = type(
        "M",
        (),
        {"content": text, "role": "assistant", "tool_calls": None, "reasoning": reasoning},
    )()
    return type("C", (), {"message": msg, "finish_reason": "stop", "index": 0})()


def test_non_streaming_reasoning_reaches_the_response() -> None:
    """The helper being right is not enough — the call site must use it."""
    from unittest.mock import MagicMock

    client = MagicMock()
    response = MagicMock()
    response.choices = [_openrouter_message("hi", "BECAUSE_REASONS")]
    response.model = "openai/gpt-5.6-luna"
    response.usage = None
    client.chat.completions.create.return_value = response

    provider = get_provider_class("groq")(api_key="sk-test")
    # The SDK is imported lazily inside `_create_client`, so there is no
    # module-level symbol to patch; the client property is the seam.
    with patch.object(type(provider), "client", property(lambda self: client)):
        result = provider.chat([{"role": "user", "content": "hi"}])

    assert result.reasoning_content == "BECAUSE_REASONS"


def test_streaming_reasoning_reaches_the_thinking_callback() -> None:
    """Streamed `delta.reasoning` must drive on_thinking_chunk."""
    from unittest.mock import MagicMock

    def chunk(reasoning=None, content=None):
        delta = type(
            "D", (), {"content": content, "reasoning": reasoning, "tool_calls": None}
        )()
        choice = type("C", (), {"delta": delta, "finish_reason": None, "index": 0})()
        return type("K", (), {"choices": [choice], "usage": None, "model": "m"})()

    client = MagicMock()
    client.chat.completions.create.return_value = iter(
        [chunk(reasoning="STEP_ONE"), chunk(content="answer")]
    )

    provider = get_provider_class("groq")(api_key="sk-test")
    thinking: list[str] = []
    with patch.object(type(provider), "client", property(lambda self: client)):
        result = provider.chat_stream_response(
            [{"role": "user", "content": "hi"}], on_thinking_chunk=thinking.append
        )

    assert "STEP_ONE" in "".join(thinking), "delta.reasoning never reached the callback"
    assert result.reasoning_content == "STEP_ONE"


# --- the Responses wire, which reports the same numbers differently -------


def test_responses_usage_parts_reconstruct_the_prompt() -> None:
    """`input_tokens` on the Responses wire also INCLUDES the cached prefix.

    It recorded the hit alongside the untouched total, so the two
    double-counted: every consumer sums input + cache_creation + cache_read,
    and a 2613-token prompt with a 2560-token hit reported 5173. That billed
    the cached portion at BOTH rates and inflated the prompt size
    `get_pricing` uses to pick a tier — which for gpt-5.6-luna can cross the
    272K boundary and select the wrong one.

    Since #783 routes by model, one OpenAIProvider can use either wire, so
    the two must agree on what the numbers mean.
    """
    from src.providers.openai_responses import build_usage_dict

    usage = build_usage_dict(
        {
            "input_tokens": 2613,
            "output_tokens": 8,
            "total_tokens": 2621,
            "input_tokens_details": {"cached_tokens": 2560},
        },
        subscription=False,
    )
    assert usage["input_tokens"] == 53
    assert usage["cache_read_input_tokens"] == 2560
    parts = (
        usage["input_tokens"]
        + usage.get("cache_creation_input_tokens", 0)
        + usage["cache_read_input_tokens"]
    )
    assert parts == 2613, f"parts sum to {parts}, not the real prompt"


def test_both_wires_report_the_same_split_for_the_same_turn() -> None:
    """Chat Completions and Responses must not disagree about one turn."""
    from src.providers.openai_responses import build_usage_dict

    chat = get_provider_class("groq")(api_key="sk-test")._build_usage_dict(
        _usage(2613, cached=2560)
    )
    responses = build_usage_dict(
        {
            "input_tokens": 2613,
            "output_tokens": 50,
            "total_tokens": 2663,
            "input_tokens_details": {"cached_tokens": 2560},
        },
        subscription=False,
    )
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        assert chat[key] == responses[key], key


def test_responses_usage_without_a_cache_hit_is_unchanged() -> None:
    from src.providers.openai_responses import build_usage_dict

    assert build_usage_dict(
        {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
        subscription=False,
    ) == {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105}


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_cache_count_cannot_kill_the_turn(bad: float) -> None:
    """`int(float('inf'))` raises OverflowError, an ArithmeticError.

    It therefore slipped past a `ValueError`-only except and escaped both
    usage builders, ending the turn. Reachable rather than theoretical:
    stdlib `json.loads` accepts a bare `Infinity` literal, so a vendor
    emitting one would take the session down over a usage number.
    """
    from src.providers.openai_responses import build_usage_dict

    base = get_provider_class("groq")(api_key="sk-test")._build_usage_dict(
        _usage(100, cached=bad, as_dict=True)
    )
    responses = build_usage_dict(
        {
            "input_tokens": 100,
            "output_tokens": 1,
            "total_tokens": 101,
            "input_tokens_details": {"cached_tokens": bad},
        },
        subscription=False,
    )
    # survives, and claims no cache rather than inventing one
    for usage in (base, responses):
        assert usage["input_tokens"] == 100
        assert "cache_read_input_tokens" not in usage

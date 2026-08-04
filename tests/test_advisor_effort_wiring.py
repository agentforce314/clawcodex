"""Advisor reasoning-effort / thinking wiring and transient-failure retry.

The client-side advisor used to send neither a thinking config nor an
effort level on either wire, so a reviewer model chosen precisely because
it reasons harder than the worker ran with thinking off at the API's
default effort. It also had no output budget headroom for thinking, and
no retry: a single 429 — routine on a subscription bucket shared with an
interactive session — ended the consultation.

Test surface:
  * effort resolution + precedence (``advisor_effort`` > ``effort`` > omit)
  * Anthropic wire: adaptive-vs-budget thinking, ``output_config.effort``,
    the xhigh clamp
  * OpenAI-compatible wire: ``extra_body.reasoning_effort``, deliberately
    NOT clamped (the xhigh allowlist holds Anthropic model names)
  * ``max_tokens`` headroom so thinking can't starve the reply
  * bounded, abort-aware retry on transient errors only
  * an AUTHORITY test that the advisor delegates to the main loop's model
    gates rather than carrying its own copy
"""

from __future__ import annotations

import types
import unittest
from typing import Any
from unittest.mock import patch

import src.config as cfg_mod
import src.providers as providers_mod
import src.settings.settings as settings_mod
import src.utils.advisor as advisor_mod
from src.query.query import build_anthropic_thinking_kwargs
from src.utils.advisor import execute_client_advisor


class _FakeResponse:
    content = "**Gaps:** none\n**Risks:** none\n**Do next:** ship it"
    usage = {"input_tokens": 11, "output_tokens": 7}
    model = "fake-model"


class _Recorder:
    """Provider double that records the kwargs it was called with."""

    def __init__(self, *, fail_with: list[Exception] | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self.messages: list[Any] = []
        self.calls = 0
        self._fail_with = list(fail_with or [])

    def __call__(self, api_key: str = "", base_url: Any = None, model: Any = None):
        self.model = model
        self.seen_api_key = api_key
        return self

    def chat_stream_response(self, messages, **kwargs):
        self.calls += 1
        self.messages = messages
        self.kwargs = kwargs
        if self._fail_with:
            raise self._fail_with.pop(0)
        return _FakeResponse()


def _run_advisor(
    model: str,
    *,
    anthropic_wire: bool,
    effort: str = "",
    advisor_effort: str = "",
    recorder: _Recorder | None = None,
    abort_signal: Any = None,
) -> tuple[tuple[bool, str, dict[str, int]], _Recorder]:
    rec = recorder or _Recorder()
    fake_settings = types.SimpleNamespace(effort=effort, advisor_effort=advisor_effort)
    with (
        patch.object(settings_mod, "get_settings", lambda: fake_settings),
        patch.object(providers_mod, "get_provider_class", lambda key: rec),
        patch.object(providers_mod, "is_anthropic_wire", lambda p: anthropic_wire),
        patch.object(
            cfg_mod, "get_provider_config",
            lambda key: {"api_key": "k", "base_url": "https://example.test"},
        ),
    ):
        result = execute_client_advisor(
            model,
            [{"role": "user", "content": "hi"}],
            advisor_provider="p",
            abort_signal=abort_signal,
        )
    return result, rec


class TestAdvisorEffortResolution(unittest.TestCase):
    """Where the advisor's effort level comes from."""

    def test_advisor_effort_overrides_session_effort(self) -> None:
        (ok, _, _), rec = _run_advisor(
            "claude-opus-5", anthropic_wire=True, effort="low", advisor_effort="max",
        )
        self.assertTrue(ok)
        self.assertEqual(rec.kwargs["output_config"], {"effort": "max"})

    def test_session_effort_inherited_when_advisor_effort_unset(self) -> None:
        (ok, _, _), rec = _run_advisor(
            "claude-opus-5", anthropic_wire=True, effort="xhigh",
        )
        self.assertTrue(ok)
        self.assertEqual(rec.kwargs["output_config"], {"effort": "xhigh"})

    def test_no_effort_anywhere_omits_the_parameter(self) -> None:
        """Omit-don't-guess: the API applies its own model default."""
        (ok, _, _), rec = _run_advisor("claude-opus-5", anthropic_wire=True)
        self.assertTrue(ok)
        self.assertNotIn("output_config", rec.kwargs)

    def test_settings_failure_does_not_break_the_consultation(self) -> None:
        def _boom():
            raise RuntimeError("settings exploded")

        rec = _Recorder()
        with (
            patch.object(settings_mod, "get_settings", _boom),
            patch.object(providers_mod, "get_provider_class", lambda key: rec),
            patch.object(providers_mod, "is_anthropic_wire", lambda p: True),
            patch.object(
                cfg_mod, "get_provider_config",
                lambda key: {"api_key": "k", "base_url": None},
            ),
        ):
            ok, _text, _usage = execute_client_advisor(
                "claude-opus-5", [{"role": "user", "content": "hi"}],
                advisor_provider="p",
            )
        self.assertTrue(ok)
        self.assertNotIn("output_config", rec.kwargs)


class TestAdvisorAnthropicWire(unittest.TestCase):
    """Thinking config + effort on the Anthropic wire."""

    def test_opus5_gets_adaptive_thinking_and_effort(self) -> None:
        (ok, _, _), rec = _run_advisor(
            "claude-opus-5", anthropic_wire=True, advisor_effort="xhigh",
        )
        self.assertTrue(ok)
        self.assertEqual(rec.kwargs["thinking"], {"type": "adaptive"})
        self.assertEqual(rec.kwargs["output_config"], {"effort": "xhigh"})

    def test_non_adaptive_model_gets_budget_thinking(self) -> None:
        """Adaptive is a hard 400 on these; budget is the safe direction."""
        (ok, _, _), rec = _run_advisor(
            "claude-haiku-4-5-20251001", anthropic_wire=True, advisor_effort="high",
        )
        self.assertTrue(ok)
        thinking = rec.kwargs["thinking"]
        self.assertEqual(thinking["type"], "enabled")
        self.assertGreaterEqual(thinking["budget_tokens"], 1024)
        self.assertLess(thinking["budget_tokens"], rec.kwargs["max_tokens"])
        # Not on the effort allowlist -> no output_config, or the API 400s.
        self.assertNotIn("output_config", rec.kwargs)

    def test_xhigh_clamped_on_models_that_reject_it(self) -> None:
        """sonnet-4-6 400s on xhigh; the shared resolver downgrades to high."""
        (ok, _, _), rec = _run_advisor(
            "claude-sonnet-4-6", anthropic_wire=True, advisor_effort="xhigh",
        )
        self.assertTrue(ok)
        self.assertEqual(rec.kwargs["output_config"], {"effort": "high"})

    def test_system_is_sent_as_blocks_not_a_string(self) -> None:
        """Load-bearing on the Claude subscription (OAuth) path.

        ``_prepare_subscription_request`` prepends the required "You are
        Claude Code…" preamble. Given a STRING it concatenates into one
        blob; given a LIST it inserts the preamble as its own block. The
        endpoint accepts only the latter for premium models — wire-probed
        against claude-opus-5 over subscription, 3/3 per cell — and the
        rejection arrives mislabelled as ``rate_limit_error``, so a
        regression here looks like a capacity problem, not a bug.
        """
        (_ok, _, _), rec = _run_advisor("claude-opus-5", anthropic_wire=True)
        system = rec.kwargs["system"]
        self.assertIsInstance(
            system, list,
            msg="advisor system reverted to a string — this 429s on "
                "subscription for premium models",
        )
        self.assertEqual(system[0]["type"], "text")
        self.assertIn("senior reviewer", system[0]["text"])

    def test_no_reasoning_effort_body_field_on_anthropic(self) -> None:
        """``extra_body.reasoning_effort`` is the OpenAI shape; on the
        Anthropic wire it is a 400 (extra inputs are not permitted)."""
        (_ok, _, _), rec = _run_advisor(
            "claude-opus-5", anthropic_wire=True, advisor_effort="xhigh",
        )
        self.assertNotIn("extra_body", rec.kwargs)


class TestAdvisorOpenAIWire(unittest.TestCase):
    """Effort on OpenAI-compatible wires."""

    def test_reasoning_effort_goes_in_extra_body(self) -> None:
        (ok, _, _), rec = _run_advisor(
            "glm-5.2", anthropic_wire=False, advisor_effort="high",
        )
        self.assertTrue(ok)
        self.assertEqual(rec.kwargs["extra_body"], {"reasoning_effort": "high"})

    def test_xhigh_is_not_clamped_on_openai_wire(self) -> None:
        """The xhigh allowlist holds Anthropic model NAMES and matches
        nothing here — clamping on it silently downgraded every xhigh."""
        (_ok, _, _), rec = _run_advisor(
            "glm-5.2", anthropic_wire=False, advisor_effort="xhigh",
        )
        self.assertEqual(rec.kwargs["extra_body"], {"reasoning_effort": "xhigh"})

    def test_no_thinking_or_output_config_on_openai_wire(self) -> None:
        (_ok, _, _), rec = _run_advisor(
            "glm-5.2", anthropic_wire=False, advisor_effort="xhigh",
        )
        self.assertNotIn("thinking", rec.kwargs)
        self.assertNotIn("output_config", rec.kwargs)

    def test_effort_omitted_when_unset(self) -> None:
        (_ok, _, _), rec = _run_advisor("glm-5.2", anthropic_wire=False)
        self.assertNotIn("extra_body", rec.kwargs)

    def test_effort_is_translated_into_the_providers_vocabulary(self) -> None:
        """The advisor must apply ``normalize_reasoning_effort`` like the main
        loop. Skipping it sent DeepSeek ``xhigh`` — a level it does not know —
        so it dropped the field and applied its own default: a SILENT
        downgrade on the setting people pick when a task is hard."""
        rec = _Recorder()
        rec.normalize_reasoning_effort = lambda e: "max" if e == "xhigh" else e
        (_ok, _, _), rec = _run_advisor(
            "deepseek-v4-pro", anthropic_wire=False,
            advisor_effort="xhigh", recorder=rec,
        )
        self.assertEqual(rec.kwargs["extra_body"], {"reasoning_effort": "max"})

    def test_a_bogus_normalization_is_discarded_not_trusted(self) -> None:
        """Duck-typed getattr: a MagicMock-ish provider answers every
        attribute with a callable, so an unguarded assignment would write a
        repr into the request body."""
        rec = _Recorder()
        rec.normalize_reasoning_effort = lambda e: object()
        (_ok, _, _), rec = _run_advisor(
            "glm-5.2", anthropic_wire=False, advisor_effort="high", recorder=rec,
        )
        self.assertEqual(rec.kwargs["extra_body"], {"reasoning_effort": "high"})

    def test_a_raising_normalizer_never_breaks_the_call(self) -> None:
        def _boom(_e):
            raise RuntimeError("provider exploded")

        rec = _Recorder()
        rec.normalize_reasoning_effort = _boom
        (ok, _, _), rec = _run_advisor(
            "glm-5.2", anthropic_wire=False, advisor_effort="high", recorder=rec,
        )
        self.assertTrue(ok)
        self.assertEqual(rec.kwargs["extra_body"], {"reasoning_effort": "high"})


class TestAdvisorOutputBudget(unittest.TestCase):
    """Thinking tokens come out of the same max_tokens budget as the reply."""

    def test_budget_never_below_the_historical_floor(self) -> None:
        for model, wire in (
            ("claude-opus-5", True),
            ("glm-5.2", False),
            ("some-unknown-model", False),
        ):
            with self.subTest(model=model):
                (_ok, _, _), rec = _run_advisor(model, anthropic_wire=wire)
                self.assertGreaterEqual(rec.kwargs["max_tokens"], 4096)

    def test_thinking_capable_model_gets_headroom_beyond_the_floor(self) -> None:
        """At a flat 4096 a high-effort reviewer can spend the whole budget
        thinking and return stop_reason=max_tokens with no text at all."""
        (_ok, _, _), rec = _run_advisor(
            "claude-opus-5", anthropic_wire=True, advisor_effort="xhigh",
        )
        self.assertGreater(rec.kwargs["max_tokens"], 4096)

    def test_anthropic_takes_the_model_table_value_whole(self) -> None:
        """On this wire max_output_tokens IS the request's max_tokens by
        design — same value the main loop sends.

        The ceiling is PATCHED DOWN for this assertion. At its real value no
        Anthropic model can distinguish clamped from unclamped — 32768 is
        itself the largest max_output_tokens in the whole Anthropic family —
        so without this patch the test passes even if the clamp is applied
        to BOTH wires, leaving the asymmetry the fix rests on unpinned.
        """
        from src.models.context import resolve_max_output_tokens

        with patch.object(advisor_mod, "_ADVISOR_MAX_OPENAI_WIRE_TOKENS", 8192):
            (_ok, _, _), rec = _run_advisor("claude-opus-5", anthropic_wire=True)
        self.assertEqual(
            rec.kwargs["max_tokens"],
            resolve_max_output_tokens(None, "claude-opus-5"),
            msg="the OpenAI-wire ceiling leaked onto the Anthropic wire",
        )

    def test_base_url_reaches_the_budget_resolver(self) -> None:
        """Per-endpoint model-limit overrides are keyed on base_url; the main
        loop passes it, so the advisor must too."""
        seen: dict[str, Any] = {}
        real = None
        import src.models.context as ctx_mod

        real = ctx_mod.resolve_max_output_tokens

        def spy(override, model_id, *, base_url=None):
            seen["base_url"] = base_url
            return real(override, model_id, base_url=base_url)

        with patch.object(ctx_mod, "resolve_max_output_tokens", spy):
            _run_advisor("claude-opus-5", anthropic_wire=True)
        self.assertEqual(seen.get("base_url"), "https://example.test")

    def test_openai_wire_is_clamped_below_the_advisory_table_value(self) -> None:
        """The table value is an auto-compact reservation on this wire, not a
        legal request cap — deepseek's row is 384000, luna's 128000, and
        openai_compatible DOES forward max_tokens to completions.create().

        DeepSeek accepts 384000 (probed), so this guards an untested number
        per provider across ~30 of them rather than a known outage — but a
        rejection would be a non-retryable 400 that degrades silently."""
        for model in ("deepseek-v4-pro", "gpt-5.6-luna"):
            with self.subTest(model=model):
                (_ok, _, _), rec = _run_advisor(model, anthropic_wire=False)
                self.assertEqual(
                    rec.kwargs["max_tokens"],
                    advisor_mod._ADVISOR_MAX_OPENAI_WIRE_TOKENS,
                )

    def test_the_ceiling_still_equals_the_anthropic_family_maximum(self) -> None:
        """The ceiling is documented as a RULE — "the OpenAI wire never gets a
        larger budget than the most generous Anthropic model" — so pin it,
        the way VALID_THINKING_EFFORT_LEVELS pins its ladder.

        This is load-bearing because the anchor is a SINGLE legacy row
        (claude-opus-4-20250514 at 32768; opus-5, opus-4-8 and fable-5 are all
        32000). Pruning old model rows would drop the family maximum to 32000
        and silently turn the constant's stated rationale into a false
        statement, with nothing else noticing.
        """
        from src.models.configs import MODEL_CONFIGS

        family_max = max(
            cfg.max_output_tokens
            for name, cfg in MODEL_CONFIGS.items()
            if "claude" in name.lower()
        )
        self.assertEqual(
            advisor_mod._ADVISOR_MAX_OPENAI_WIRE_TOKENS, family_max,
            msg=(
                "the Anthropic family maximum moved — either update "
                "_ADVISOR_MAX_OPENAI_WIRE_TOKENS to match, or rewrite the "
                "constant's comment to stop claiming it equals that maximum"
            ),
        )

    def test_clamp_never_raises_a_smaller_model_budget(self) -> None:
        """The clamp is a ceiling, not a floor — a model whose table value is
        already modest keeps it."""
        (_ok, _, _), rec = _run_advisor("glm-5.2", anthropic_wire=False)
        self.assertLess(
            rec.kwargs["max_tokens"],
            advisor_mod._ADVISOR_MAX_OPENAI_WIRE_TOKENS,
        )
        self.assertGreaterEqual(rec.kwargs["max_tokens"], 4096)


class TestAdvisorRetry(unittest.TestCase):
    """Bounded retry on transient failures only."""

    def _rate_limit(self) -> Exception:
        exc = Exception("Error code: 429 - rate_limit_error")
        setattr(exc, "status_code", 429)
        return exc

    def test_transient_failure_is_retried_and_can_succeed(self) -> None:
        rec = _Recorder(fail_with=[self._rate_limit()])
        with patch.object(advisor_mod, "_advisor_sleep", lambda d, s: True):
            (ok, text, usage), rec = _run_advisor(
                "claude-opus-5", anthropic_wire=True, recorder=rec,
            )
        self.assertTrue(ok, msg=text)
        self.assertEqual(rec.calls, 2)
        self.assertEqual(usage["input_tokens"], 11)

    def test_retries_are_bounded(self) -> None:
        rec = _Recorder(fail_with=[self._rate_limit() for _ in range(10)])
        with patch.object(advisor_mod, "_advisor_sleep", lambda d, s: True):
            (ok, text, usage), rec = _run_advisor(
                "claude-opus-5", anthropic_wire=True, recorder=rec,
            )
        self.assertFalse(ok)
        self.assertEqual(rec.calls, advisor_mod._ADVISOR_MAX_ATTEMPTS)
        self.assertEqual(usage, {"input_tokens": 0, "output_tokens": 0})

    def test_non_retryable_error_fails_on_first_attempt(self) -> None:
        """A bad request must not be re-issued three times."""
        exc = Exception("Error code: 400 - invalid_request_error: bad model")
        setattr(exc, "status_code", 400)
        rec = _Recorder(fail_with=[exc, exc, exc])
        with patch.object(advisor_mod, "_advisor_sleep", lambda d, s: True):
            (ok, _text, _usage), rec = _run_advisor(
                "claude-opus-5", anthropic_wire=True, recorder=rec,
            )
        self.assertFalse(ok)
        self.assertEqual(rec.calls, 1)

    def test_abort_during_backoff_stops_retrying(self) -> None:
        """ESC must not be stuck behind the advisor's backoff."""
        rec = _Recorder(fail_with=[self._rate_limit() for _ in range(5)])
        with patch.object(advisor_mod, "_advisor_sleep", lambda d, s: False):
            (ok, _text, _usage), rec = _run_advisor(
                "claude-opus-5", anthropic_wire=True, recorder=rec,
            )
        self.assertFalse(ok)
        self.assertEqual(rec.calls, 1)

    def test_advisor_sleep_returns_false_once_aborted(self) -> None:
        signal = types.SimpleNamespace(aborted=True)
        self.assertFalse(advisor_mod._advisor_sleep(5.0, signal))

    def test_advisor_sleep_completes_without_abort(self) -> None:
        self.assertTrue(advisor_mod._advisor_sleep(0.01, None))


class TestSharedGateAuthority(unittest.TestCase):
    """The advisor must DELEGATE to the main loop's gates, not copy them.

    Both call sites read the same model allowlists; a future edit that
    inlines a private copy into advisor.py would keep every assertion above
    green while silently reintroducing drift. Patch the gate at its single
    source and require the advisor's wire output to follow.
    """

    def test_effort_gate_is_the_main_loops(self) -> None:
        with patch("src.query.query._model_supports_effort", lambda m: False):
            (_ok, _, _), rec = _run_advisor(
                "claude-opus-5", anthropic_wire=True, advisor_effort="xhigh",
            )
        self.assertNotIn(
            "output_config", rec.kwargs,
            msg="advisor ignored the main loop's effort gate — it has its "
                "own copy of the allowlist and will drift",
        )

    def test_adaptive_thinking_gate_is_the_main_loops(self) -> None:
        with patch("src.query.query._model_supports_adaptive_thinking", lambda m: False):
            (_ok, _, _), rec = _run_advisor(
                "claude-opus-5", anthropic_wire=True, advisor_effort="xhigh",
            )
        self.assertEqual(
            rec.kwargs["thinking"]["type"], "enabled",
            msg="advisor ignored the main loop's adaptive-thinking gate",
        )


class TestAdvisorCredentialResolution(unittest.TestCase):
    """The advisor must resolve its provider key the way every other call
    site does — configured value first, then the environment."""

    def _run_with_config(self, provider_cfg: dict, env: dict) -> _Recorder:
        rec = _Recorder()
        fake_settings = types.SimpleNamespace(effort="", advisor_effort="")
        with (
            patch.object(settings_mod, "get_settings", lambda: fake_settings),
            patch.object(providers_mod, "get_provider_class", lambda key: rec),
            patch.object(providers_mod, "is_anthropic_wire", lambda p: False),
            patch.object(cfg_mod, "get_provider_config", lambda key: provider_cfg),
            patch.dict("os.environ", env, clear=False),
        ):
            execute_client_advisor(
                "glm-5.2", [{"role": "user", "content": "hi"}],
                advisor_provider="zai",
            )
        return rec

    def test_key_falls_back_to_the_environment(self) -> None:
        """An advisor provider whose key lives in an env var — how eval
        containers and most shells supply credentials — must authenticate.
        Reading providers.<name>.api_key directly returned "" and the call
        died on 'Missing credentials'."""
        rec = self._run_with_config(
            {"api_key": "", "base_url": None}, {"ZAI_API_KEY": "from-env"},
        )
        self.assertEqual(rec.model, "glm-5.2")
        # No getattr default — a missing attribute must fail loudly rather
        # than pass by falling back to the value under test.
        self.assertEqual(
            rec.seen_api_key, "from-env",
            msg="advisor ignored the environment for its provider key",
        )

    def test_configured_key_outranks_the_environment(self) -> None:
        """Precedence must match resolve_api_key: config wins. This is what
        keeps an exported ANTHROPIC_API_KEY from silently outranking a
        subscription OAuth login in the opposite direction."""
        from src.providers import resolve_api_key

        self.assertEqual(
            resolve_api_key("zai", {"api_key": "from-config"}), "from-config",
        )

    def test_empty_key_survives_for_the_oauth_fallback(self) -> None:
        """The Anthropic subscription path REQUIRES an empty api_key so the
        provider falls through to OAuth — resolution must not invent one."""
        from src.providers import resolve_api_key

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                resolve_api_key("anthropic", {"api_key": "", "base_url": None}), "",
            )


class TestSubscriptionSystemShape(unittest.TestCase):
    """The mechanism behind the block-list requirement, pinned at the
    provider seam rather than asserted only at the advisor's call site."""

    def _provider(self):
        from src.providers.anthropic_provider import AnthropicProvider

        p = AnthropicProvider(api_key="k", model="claude-opus-5")
        p._subscription_token = "fake-oauth-token"  # engage the OAuth path
        return p

    def test_block_list_keeps_the_preamble_as_its_own_block(self) -> None:
        p = self._provider()
        _msgs, _tools, system = p._prepare_subscription_request(
            [{"role": "user", "content": "hi"}],
            None,
            [{"type": "text", "text": "You are a senior reviewer."}],
        )
        self.assertIsInstance(system, list)
        self.assertIn("Claude Code", system[0]["text"])
        # The preamble must stand alone — a premium model rejects the
        # request when the advisor text is fused into that same block.
        self.assertNotIn("senior reviewer", system[0]["text"])
        self.assertIn("senior reviewer", system[1]["text"])

    def test_string_system_fuses_the_preamble_with_the_prompt(self) -> None:
        """Documents the shape that gets rejected, so the reason the
        advisor sends blocks stays visible to the next reader."""
        p = self._provider()
        _msgs, _tools, system = p._prepare_subscription_request(
            [{"role": "user", "content": "hi"}], None, "You are a senior reviewer.",
        )
        self.assertIsInstance(system, str)
        self.assertIn("Claude Code", system)
        self.assertIn("senior reviewer", system)

    def test_advisor_shape_survives_the_subscription_rewrite(self) -> None:
        """End-to-end on the shape: what execute_client_advisor builds must
        still be a preamble-first block list after the provider rewrite."""
        (_ok, _, _), rec = _run_advisor("claude-opus-5", anthropic_wire=True)
        p = self._provider()
        _m, _t, system = p._prepare_subscription_request(
            [{"role": "user", "content": "hi"}], None, rec.kwargs["system"],
        )
        self.assertIsInstance(system, list)
        self.assertIn("Claude Code", system[0]["text"])
        self.assertNotIn("senior reviewer", system[0]["text"])


class TestBuildAnthropicThinkingKwargs(unittest.TestCase):
    """The extracted builder itself (shared by both call sites)."""

    def test_returns_empty_for_non_thinking_model(self) -> None:
        self.assertEqual(build_anthropic_thinking_kwargs("gpt-4o"), {})

    def test_force_thinking_enables_an_unrecognised_alias(self) -> None:
        out = build_anthropic_thinking_kwargs(
            "some-unknown-alias", max_tokens=8192, force_thinking=True,
        )
        self.assertEqual(out["thinking"]["type"], "enabled")

    def test_budget_omitted_when_max_tokens_too_small(self) -> None:
        """No budget in [1024, max_tokens) fits — omit rather than 400."""
        out = build_anthropic_thinking_kwargs(
            "claude-haiku-4-5-20251001", max_tokens=512,
        )
        self.assertNotIn("thinking", out)

    def test_adaptive_model_ignores_max_tokens(self) -> None:
        out = build_anthropic_thinking_kwargs("claude-opus-5", max_tokens=0)
        self.assertEqual(out["thinking"], {"type": "adaptive"})


if __name__ == "__main__":
    unittest.main()


class TestAdvisorEmptyResponse(unittest.TestCase):
    """An empty reply is usually the reviewer DECLINING, not a glitch.

    Across an 89-task terminal-bench run every empty response came from a
    security-flavoured task, the model emitted 2-3 tokens, and 100% of
    consultations on those tasks failed — so the worker called again and got
    the same non-answer, spending a turn and a full cache-write each time.
    """

    def _empty_response_recorder(self, finish_reason: str = "") -> _Recorder:
        class _Empty:
            content = "   "
            usage = {"input_tokens": 5, "output_tokens": 2}
            model = "claude-opus-5"

        _Empty.finish_reason = finish_reason
        rec = _Recorder()
        rec_cls_response = _Empty()

        def chat_stream_response(messages, **kwargs):
            rec.calls += 1
            rec.kwargs = kwargs
            return rec_cls_response

        rec.chat_stream_response = chat_stream_response
        return rec

    def test_empty_reply_explains_itself_and_says_not_to_retry(self) -> None:
        rec = self._empty_response_recorder("refusal")
        (ok, text, _usage), rec = _run_advisor(
            "claude-opus-5", anthropic_wire=True, recorder=rec,
        )
        self.assertFalse(ok)
        self.assertIn("refusal", text, msg="the stop reason must be carried")
        self.assertIn("declined", text.lower())
        self.assertIn("unlikely to help", text.lower())

    def test_empty_reply_is_not_retried(self) -> None:
        """A considered non-answer is not transient; re-issuing costs a fresh
        consultation to reach the same place."""
        rec = self._empty_response_recorder("end_turn")
        (_ok, _text, _u), rec = _run_advisor(
            "claude-opus-5", anthropic_wire=True, recorder=rec,
        )
        self.assertEqual(rec.calls, 1)

    def test_usage_is_still_reported_for_an_empty_reply(self) -> None:
        """The call still cost tokens; dropping them would hide the spend."""
        rec = self._empty_response_recorder("refusal")
        (_ok, _text, usage), _rec = _run_advisor(
            "claude-opus-5", anthropic_wire=True, recorder=rec,
        )
        self.assertEqual(usage["input_tokens"], 5)
        self.assertEqual(usage["output_tokens"], 2)

    def test_missing_stop_reason_still_produces_actionable_text(self) -> None:
        rec = self._empty_response_recorder("")
        (_ok, text, _u), _rec = _run_advisor(
            "claude-opus-5", anthropic_wire=True, recorder=rec,
        )
        self.assertIn("declined", text.lower())
        self.assertNotIn("stop reason:", text.lower())


class TestAdvisorAlwaysAsksForAdvice(unittest.TestCase):
    """The forwarded conversation must always END WITH A REQUEST.

    The tail-is-user case is the COMMON one — the worker calls the advisor
    mid-tool-round, so after flattening the last turn is a `[Tool result:
    ...]` line. The request used to be appended only when the tail was NOT
    already a user turn, so in the common case the reviewer got a bare
    unterminated transcript and CONTINUED it, replying in the worker's voice
    with narration and tool calls instead of a review.

    Measured on an 89-task run: 54 of 326 answered consultations (16.6%),
    across 45 of 89 tasks.
    """

    def _fwd(self, history):
        from src.utils.advisor import build_advisor_forwarded_messages

        return build_advisor_forwarded_messages(history)

    def _suffix(self) -> str:
        from src.utils.advisor import CLIENT_ADVISOR_PROMPT_SUFFIX

        return CLIENT_ADVISOR_PROMPT_SUFFIX

    def test_tail_is_a_user_tool_result_still_asks(self) -> None:
        """THE regression. This shape produced 16.6% junk consultations."""
        history = [
            {"role": "user", "content": "do the task"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "waiting on the build"},
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "file.txt"},
            ]},
        ]
        fwd = self._fwd(history)
        self.assertEqual(fwd[-1]["role"], "user")
        self.assertIn(
            self._suffix(), fwd[-1]["content"],
            msg="reviewer received a transcript with no request — it will "
                "continue the transcript instead of reviewing",
        )

    def test_the_original_tool_result_text_is_preserved(self) -> None:
        history = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "IMPORTANT"}]},
        ]
        fwd = self._fwd(history)
        self.assertIn("IMPORTANT", fwd[-1]["content"])
        self.assertIn(self._suffix(), fwd[-1]["content"])

    def test_tail_is_assistant_appends_a_user_turn(self) -> None:
        """The original behaviour, unchanged."""
        history = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "my plan is X"}]},
        ]
        fwd = self._fwd(history)
        self.assertEqual(fwd[-1]["role"], "user")
        self.assertEqual(fwd[-1]["content"], self._suffix())

    def test_no_two_consecutive_user_turns(self) -> None:
        """Some wires reject consecutive same-role messages, so the request
        is folded into the existing turn rather than added after it."""
        history = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "out"}]},
        ]
        fwd = self._fwd(history)
        roles = [m["role"] for m in fwd]
        for a, b in zip(roles, roles[1:]):
            self.assertNotEqual(a, b, msg=f"consecutive {a} turns: {roles}")

    def test_request_is_not_duplicated(self) -> None:
        history = [{"role": "user", "content": "task"}]
        once = self._fwd(history)
        self.assertEqual(once[-1]["content"].count(self._suffix()), 1)

    def test_every_shape_ends_with_a_user_request(self) -> None:
        shapes = {
            "empty": [],
            "user only": [{"role": "user", "content": "task"}],
            "assistant tail": [
                {"role": "user", "content": "t"},
                {"role": "assistant", "content": [{"type": "text", "text": "plan"}]}],
            "tool-result tail": [
                {"role": "user", "content": "t"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "x", "name": "Bash", "input": {}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "x", "content": "o"}]}],
        }
        for name, hist in shapes.items():
            with self.subTest(shape=name):
                fwd = self._fwd(hist)
                self.assertEqual(fwd[-1]["role"], "user")
                self.assertIn(self._suffix(), fwd[-1]["content"])

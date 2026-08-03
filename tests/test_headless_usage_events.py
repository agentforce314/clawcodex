"""Incremental usage events keep a KILLED run measurable.

``ResultEvent`` carries the authoritative usage but only exists if the run
reaches the end. A run killed by an eval ceiling / SIGKILL / dropped
connection emitted nothing, so everything it spent was unmeasurable —
21 of 46 trials in one terminal-bench job (2026-08-02), and precisely the
longest and most expensive ones, since those are what time out.
"""

from __future__ import annotations

import json

from src.cli_core import ResultEvent, StreamJsonWriter, UsageEvent


class _Sink:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def flush(self) -> None:
        pass

    def events(self) -> list[dict]:
        out = []
        for line in "".join(self.lines).splitlines():
            line = line.strip()
            if line.startswith("{"):
                out.append(json.loads(line))
        return out


def test_usage_event_serializes_with_a_distinct_type():
    sink = _Sink()
    StreamJsonWriter(sink).write(
        UsageEvent(
            usage={"input_tokens": 10, "output_tokens": 3}, assistant_messages=2
        )
    )
    (event,) = sink.events()
    assert event["type"] == "usage"
    assert event["usage"] == {"input_tokens": 10, "output_tokens": 3}
    assert event["assistant_messages"] == 2
    assert "num_turns" not in event, (
        "num_turns was structurally always 0 here and the adapter promoted "
        "that zero into killed trials' metrics"
    )


def test_usage_event_does_not_collide_with_the_result_event():
    """Additive: the terminal result event is unchanged, so a consumer that
    only knows ``result`` keeps working exactly as before."""
    sink = _Sink()
    writer = StreamJsonWriter(sink)
    writer.write(UsageEvent(usage={"input_tokens": 1}))
    writer.write(ResultEvent(session_id="s", num_turns=1, result="done"))
    types = [e["type"] for e in sink.events()]
    assert types == ["usage", "result"]
    assert sink.events()[1]["subtype"] == "success"


# --- the harbor adapter's fallback chain ----------------------------------


def _adapter():
    """The adapter class without running Harbor's ``__init__``.

    Skipped where it cannot be imported. The adapter is eval-only tooling that
    runs under Harbor's own uv-managed CPython 3.13 with harbor installed; it
    uses ``typing.override`` (3.12+) and imports ``harbor.*``, so the repo's
    3.11 test venv cannot load it. Run these under that interpreter:

        uv run --isolated --python 3.13 --with harbor --with pytest \\
            python -m pytest tests/test_headless_usage_events.py

    ``--isolated`` is REQUIRED, not tidiness. Without it ``uv run`` treats the
    repo as a project and re-syncs ``.venv`` to the requested interpreter —
    it replaced this repo's 3.11 venv with a 3.13 one (and on a second
    invocation deleted it outright), which then has to be rebuilt from
    ``requirements.txt`` + ``requirements.dev.txt``. ``--with pytest`` is also
    required: the isolated env has no dev dependencies.
    """
    import sys
    from pathlib import Path

    import pytest

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval" / "harbor"))
    try:
        from clawcodex_agent import Clawcodex
    except Exception as exc:  # noqa: BLE001 — environment, not a failure
        pytest.skip(f"harbor adapter not importable here: {exc}")

    return Clawcodex


def test_last_usage_event_is_the_cumulative_one():
    Clawcodex = _adapter()
    events = [
        {"type": "usage", "usage": {"input_tokens": 5}},
        {"type": "tool_use", "name": "Bash"},
        {"type": "usage", "usage": {"input_tokens": 40}},
        {"type": "tool_use", "name": "Read"},
    ]
    found = Clawcodex._last_usage_event(events)
    assert found["usage"]["input_tokens"] == 40, "must take the LAST, not the first"

    assert Clawcodex._last_usage_event([{"type": "tool_use"}]) is None
    # A malformed usage payload must not be mistaken for a real one.
    assert Clawcodex._last_usage_event([{"type": "usage", "usage": "nope"}]) is None


def test_usage_columns_counts_cached_tokens():
    """``input_tokens`` is only the NON-cached part — a total built from
    input+output alone silently omits every cached token (the #786 bug)."""
    Clawcodex = _adapter()
    prompt, cached, completion = Clawcodex._usage_columns({
        "input_tokens": 100,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 50,
        "output_tokens": 7,
    })
    assert prompt == 1050
    assert cached == 900
    assert completion == 7


def test_final_metrics_falls_back_to_the_usage_event():
    """The killed-trial lane: no session totals, no result event."""
    Clawcodex = _adapter()
    adapter = Clawcodex.__new__(Clawcodex)
    metrics = Clawcodex._final_metrics(
        adapter,
        None,           # result_event — a killed run never emits one
        12,             # total_steps
        None,           # totals — copy-back never ran
        {"type": "usage", "assistant_messages": 4,
         "usage": {"input_tokens": 100, "cache_read_input_tokens": 900,
                   "output_tokens": 7}},
    )
    assert metrics is not None, "a killed trial must still report tokens"
    assert metrics.total_prompt_tokens == 1000
    assert metrics.total_completion_tokens == 7
    # Round trips are surfaced under their OWN name. Mapping them onto
    # num_turns would report a confidently wrong turn count for exactly the
    # killed trials this lane exists to measure.
    assert metrics.extra.get("assistant_messages") == 4
    assert "num_turns" not in metrics.extra


def test_final_metrics_prefers_the_result_event_over_the_usage_event():
    """Lane order matters: the usage event is a LAST resort, never a
    replacement for the authoritative terminal numbers."""
    Clawcodex = _adapter()
    adapter = Clawcodex.__new__(Clawcodex)
    metrics = Clawcodex._final_metrics(
        adapter,
        {"type": "result", "usage": {"input_tokens": 999, "output_tokens": 99},
         "num_turns": 9},
        3,
        None,
        {"type": "usage", "usage": {"input_tokens": 1, "output_tokens": 1}},
    )
    assert metrics.total_prompt_tokens == 999
    assert metrics.total_completion_tokens == 99


def test_final_metrics_still_returns_none_with_no_source_at_all():
    Clawcodex = _adapter()
    adapter = Clawcodex.__new__(Clawcodex)
    assert Clawcodex._final_metrics(adapter, None, 0, None, None) is None

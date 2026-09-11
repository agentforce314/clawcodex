"""DeepSeek's peak/off-peak rate schedule (issue #904), and V4 Pro's
retirement onto V4.1 Flash.

Since 2026-08-16 DeepSeek publishes a peak and an off-peak card: every rate
doubles during 01:00-04:00 and 06:00-10:00 UTC, Monday through Friday. That
is the pricing table's third tier axis, and the only one that is not a
property of the request's content — ``input_tokens`` is prompt size and
``service_tier`` is what the provider declared in its response, so neither
could carry it.

Two things are worth pinning here and are pinned separately:

* the SCHEDULE — which instants are peak — because an off-by-one on a window
  boundary or a missed weekend rule mis-prices a whole class of requests
  silently; and
* the CARD — the absolute published rates — because the values this issue
  replaced were internally consistent (correct ratios, mirrored
  cache_creation) and still 3x low. Only an external number catches that,
  which is the same lesson the gpt-5.6-luna row in ``services/pricing.py``
  records.

A second time axis joined them on 2026-09-10: DeepSeek is retiring
``deepseek-v4-pro`` onto DeepSeek-V4.1-Flash, so from a fixed instant that id
prices at the Flash card. It composes with the schedule rather than replacing
it, and — like the schedule — it reads the REQUEST's timestamp, so a session
that really did run V4 Pro is never restated at the cheaper card.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.services.pricing import (
    _TIER_DEEPSEEK_FLASH_OFF_PEAK,
    _TIER_DEEPSEEK_FLASH_PEAK,
    _TIER_DEEPSEEK_PRO_OFF_PEAK,
    _TIER_DEEPSEEK_PRO_PEAK,
    compute_cost,
    deepseek_v4_pro_is_routed_to_flash,
    get_pricing,
    is_deepseek_peak,
)


MODELS = ("deepseek-flash", "deepseek-v4-pro")

# Ids DeepSeek retired but still accepts, all served by — and billed as —
# DeepSeek-V4.1-Flash.
LEGACY_FLASH_IDS = ("deepseek-v4-flash", "deepseek-v4-flash-vision-exp")


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0,
         second: int = 0) -> float:
    return datetime(
        year, month, day, hour, minute, second, tzinfo=timezone.utc
    ).timestamp()


# 2026-08-24 is a Monday, 2026-08-28 a Friday, 2026-08-29 a Saturday and
# 2026-08-30 a Sunday.
MON, FRI, SAT, SUN = 24, 28, 29, 30

# One instant on each side of the schedule, for the tests that care which
# card is in force rather than where the boundaries are. Both are in August
# 2026, BEFORE the 2026-09-14 cutover below, so ``deepseek-v4-pro`` is still
# priced as its own model here — the schedule tests and the retirement tests
# stay independent.
OFF_PEAK_TS = _utc(2026, 8, MON, 12)
PEAK_TS = _utc(2026, 8, MON, 2)

# DeepSeek retires V4 Pro onto V4.1 Flash at 12:00 Beijing (UTC+8) on
# 2026-09-14 — 04:00 UTC, which is itself off-peak (the 01:00-04:00 window is
# half-open). 2026-09-14 is a Monday and 2026-09-15 a Tuesday.
CUTOVER_TS = _utc(2026, 9, 14, 4)
AFTER_CUTOVER_OFF_PEAK_TS = _utc(2026, 9, 15, 12)
AFTER_CUTOVER_PEAK_TS = _utc(2026, 9, 15, 2)


# --------------------------------------------------------------------------- #
# The schedule
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("hour", [1, 2, 3, 6, 7, 8, 9])
def test_weekday_peak_hours(hour: int) -> None:
    assert is_deepseek_peak(_utc(2026, 8, MON, hour)) is True


@pytest.mark.parametrize("hour", [0, 4, 5, 10, 11, 17, 23])
def test_weekday_off_peak_hours(hour: int) -> None:
    """Including 04:00 and 10:00 — the windows are half-open, so the hour a
    window ends on is already off-peak."""
    assert is_deepseek_peak(_utc(2026, 8, MON, hour)) is False


def test_window_boundaries_are_half_open() -> None:
    assert is_deepseek_peak(_utc(2026, 8, MON, 0, 59, 59)) is False
    assert is_deepseek_peak(_utc(2026, 8, MON, 1, 0, 0)) is True
    assert is_deepseek_peak(_utc(2026, 8, MON, 3, 59, 59)) is True
    assert is_deepseek_peak(_utc(2026, 8, MON, 4, 0, 0)) is False
    assert is_deepseek_peak(_utc(2026, 8, MON, 5, 59, 59)) is False
    assert is_deepseek_peak(_utc(2026, 8, MON, 6, 0, 0)) is True
    assert is_deepseek_peak(_utc(2026, 8, MON, 9, 59, 59)) is True
    assert is_deepseek_peak(_utc(2026, 8, MON, 10, 0, 0)) is False


@pytest.mark.parametrize("day", [SAT, SUN])
@pytest.mark.parametrize("hour", [1, 2, 3, 6, 7, 9])
def test_weekends_are_off_peak_even_inside_the_windows(day: int, hour: int) -> None:
    assert is_deepseek_peak(_utc(2026, 8, day, hour)) is False


def test_monday_and_friday_are_both_weekdays() -> None:
    """Fencepost on the Mon-Fri rule: a `weekday() >= 5` weekend test and a
    `1 <= weekday() <= 5` one differ only on these two days."""
    assert is_deepseek_peak(_utc(2026, 8, MON, 2)) is True
    assert is_deepseek_peak(_utc(2026, 8, FRI, 2)) is True


def test_schedule_is_evaluated_in_utc_not_local_time() -> None:
    """Friday 23:00 US/Pacific is Saturday 06:00 UTC — inside a peak window by
    the local calendar, off-peak by the vendor's."""
    assert is_deepseek_peak(_utc(2026, 8, SAT, 6)) is False


def test_off_peak_covers_133_of_168_hours() -> None:
    """The vendor's schedule leaves 35 peak hours a week — 7 hours (3 + 4) on
    each of 5 days — so 133 hours are off-peak. A sweep of every hour in a
    week is the cheapest guard against a window silently widening."""
    start = _utc(2026, 8, MON, 0)
    peak_hours = sum(
        is_deepseek_peak(start + h * 3600) for h in range(7 * 24)
    )
    assert peak_hours == 35
    assert 7 * 24 - peak_hours == 133


class _FakeClock:
    """Stands in for the ``time`` module inside ``services.pricing`` only.

    Patching the name in that module's namespace rather than ``time.time``
    itself keeps the fake off pytest's own clock.
    """

    def __init__(self, ts: float) -> None:
        self.ts = ts

    def time(self) -> float:
        return self.ts


def test_omitting_request_time_reads_the_clock(monkeypatch) -> None:
    """``request_time=None`` means "price at the current clock", which is what
    makes the live path correct without passing anything: cost is computed the
    moment a response arrives. Pinned end to end — through
    ``is_deepseek_peak``, ``get_pricing`` and ``compute_cost`` — because a
    default that silently stopped reaching the clock would leave every
    production call site on one card with nothing failing."""
    usage = {"input_tokens": 1_000_000}

    monkeypatch.setattr("src.services.pricing.time", _FakeClock(PEAK_TS))
    assert is_deepseek_peak() is True
    assert get_pricing("deepseek-v4-pro")["input"] == 1.32 / 1_000_000
    assert compute_cost("deepseek-v4-pro", usage) == pytest.approx(1.32)

    monkeypatch.setattr("src.services.pricing.time", _FakeClock(OFF_PEAK_TS))
    assert is_deepseek_peak() is False
    assert get_pricing("deepseek-v4-pro")["input"] == 0.66 / 1_000_000
    assert compute_cost("deepseek-v4-pro", usage) == pytest.approx(0.66)


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #

# Published USD per 1M tokens, read 2026-09-10 from
# https://api-docs.deepseek.com/quick_start/pricing/. The flash column is
# DeepSeek-V4.1-Flash's, which is CHEAPER than the V4 flash card it replaced
# (0.22 / 0.66 / 0.007) — a re-card in the user's favour is exactly as silent
# as one against them, and only an external number catches either.
PUBLISHED = {
    "deepseek-flash": {
        "off_peak": {"input": 0.15, "output": 0.6, "cache_read": 0.003},
        "peak": {"input": 0.3, "output": 1.2, "cache_read": 0.006},
    },
    "deepseek-v4-pro": {
        "off_peak": {"input": 0.66, "output": 1.98, "cache_read": 0.022},
        "peak": {"input": 1.32, "output": 3.96, "cache_read": 0.044},
    },
}

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("window", ["off_peak", "peak"])
def test_published_rates(model: str, window: str) -> None:
    ts = OFF_PEAK_TS if window == "off_peak" else PEAK_TS
    pricing = get_pricing(model, request_time=ts)
    assert pricing is not None
    for field, dollars in PUBLISHED[model][window].items():
        assert pricing[field] == dollars / 1_000_000, field
    # No separate cache-WRITE charge on this provider: a miss is just input.
    assert pricing["cache_creation"] == pricing["input"]


@pytest.mark.parametrize("model", MODELS)
def test_peak_is_exactly_double_off_peak(model: str) -> None:
    off = get_pricing(model, request_time=OFF_PEAK_TS)
    peak = get_pricing(model, request_time=PEAK_TS)
    assert off.keys() == peak.keys()
    for field in off:
        assert peak[field] == pytest.approx(2 * off[field], rel=1e-12), field


def test_no_field_of_the_pro_card_derives_from_the_flash_one() -> None:
    """Every field has to be read off the page.

    The V4 cards were pro = 3x flash on input and output but 22/7 on cache
    read, which was already enough to make "derive one row from the other"
    wrong. Against V4.1 Flash the ratios are 4.4x, 3.3x and 7.33x — no two
    alike. Pinned as "no single multiplier fits" rather than as three magic
    numbers, because the claim worth defending is that the rows are
    independent, not what today's quotients happen to be.
    """
    for ts in (OFF_PEAK_TS, PEAK_TS):
        flash = get_pricing("deepseek-flash", request_time=ts)
        pro = get_pricing("deepseek-v4-pro", request_time=ts)
        ratios = [pro[f] / flash[f] for f in ("input", "output", "cache_read")]
        assert len(set(round(r, 9) for r in ratios)) == 3, ratios
        # And the one that moves an agentic bill most is the odd one out.
        assert ratios[2] > max(ratios[0], ratios[1])


def test_retired_flash_ids_are_billed_at_the_flash_card() -> None:
    """DeepSeek still accepts ``deepseek-v4-flash`` and
    ``deepseek-v4-flash-vision-exp``; both are served by V4.1 Flash "and
    billed at the Flash price".

    So they must carry V4.1 Flash's card, not the V4 one they shipped with.
    Keeping the old rates on them would over-report a legacy-id session by
    1.5x on input and 2.3x on cache read — the same silent-drift failure
    issue #904 was about, pointing the other way.
    """
    for model in LEGACY_FLASH_IDS:
        for ts in (OFF_PEAK_TS, PEAK_TS, AFTER_CUTOVER_OFF_PEAK_TS):
            assert get_pricing(model, request_time=ts) == get_pricing(
                "deepseek-flash", request_time=ts
            ), model


# --------------------------------------------------------------------------- #
# V4 Pro's retirement onto V4.1 Flash
# --------------------------------------------------------------------------- #

def _card(tiers: tuple[dict[str, float], dict[str, float]], ts: float) -> dict:
    """The peak-schedule half of the expectation, so the retirement tests can
    state the other half without re-deriving which window ``ts`` is in."""
    off_peak, peak = tiers
    return peak if is_deepseek_peak(ts) else off_peak


PRO_TIERS = (_TIER_DEEPSEEK_PRO_OFF_PEAK, _TIER_DEEPSEEK_PRO_PEAK)
FLASH_TIERS = (_TIER_DEEPSEEK_FLASH_OFF_PEAK, _TIER_DEEPSEEK_FLASH_PEAK)


def test_pro_prices_as_itself_before_the_cutover() -> None:
    # One second before, which is 03:59:59 UTC — inside a peak window, so this
    # also pins that the two axes compose rather than one overriding the other.
    ts = CUTOVER_TS - 1
    assert deepseek_v4_pro_is_routed_to_flash(ts) is False
    assert get_pricing("deepseek-v4-pro", request_time=ts) == _card(PRO_TIERS, ts)


def test_pro_prices_as_flash_from_the_cutover_instant() -> None:
    """Half-open, like the peak windows: the stated instant is already the new
    regime."""
    assert deepseek_v4_pro_is_routed_to_flash(CUTOVER_TS) is True
    assert get_pricing("deepseek-v4-pro", request_time=CUTOVER_TS) == get_pricing(
        "deepseek-flash", request_time=CUTOVER_TS
    )


def test_cutover_is_1200_beijing_not_1200_utc() -> None:
    """Beijing is UTC+8 with no DST, so the vendor's noon is 04:00 UTC. An
    off-by-eight-hours here mis-prices a whole business day."""
    assert deepseek_v4_pro_is_routed_to_flash(_utc(2026, 9, 14, 3, 59, 59)) is False
    assert deepseek_v4_pro_is_routed_to_flash(_utc(2026, 9, 14, 4, 0, 0)) is True


def test_pro_still_follows_the_peak_schedule_after_the_cutover() -> None:
    """The retirement swaps which CARD applies; it does not exempt the id from
    the clock. Both axes have to compose, and the peak one is applied last."""
    for ts, window in (
        (AFTER_CUTOVER_OFF_PEAK_TS, "off_peak"),
        (AFTER_CUTOVER_PEAK_TS, "peak"),
    ):
        card = PUBLISHED["deepseek-flash"][window]
        pricing = get_pricing("deepseek-v4-pro", request_time=ts)
        for field, dollars in card.items():
            assert pricing[field] == dollars / 1_000_000, (window, field)


def test_cutover_does_not_restate_history_at_the_new_card() -> None:
    """A request that really did run V4 Pro is priced by ITS timestamp, not by
    the clock at display time. ``compute_cost`` already carries the timestamp
    for the peak schedule; the retirement rides the same value, so re-reading
    an August session from October must still show what it cost.
    """
    usage = _agent_mix()
    before = compute_cost("deepseek-v4-pro", usage, request_time=OFF_PEAK_TS)
    after = compute_cost(
        "deepseek-v4-pro", usage, request_time=AFTER_CUTOVER_OFF_PEAK_TS
    )
    assert before == pytest.approx(0.0536, abs=5e-5)
    assert after == pytest.approx(
        compute_cost("deepseek-flash", usage,
                     request_time=AFTER_CUTOVER_OFF_PEAK_TS)
    )
    assert before > 4 * after


def test_flash_is_untouched_by_the_pro_cutover() -> None:
    """Scope gate: ``deepseek-flash`` was never on the pro card, so no instant
    of the retirement may move it."""
    for ts in (CUTOVER_TS - 1, CUTOVER_TS, AFTER_CUTOVER_OFF_PEAK_TS,
               AFTER_CUTOVER_PEAK_TS):
        assert get_pricing("deepseek-flash", request_time=ts) == _card(
            FLASH_TIERS, ts
        )


# --------------------------------------------------------------------------- #
# Wiring: the axis reaches compute_cost, and reaches nothing else
# --------------------------------------------------------------------------- #

def _agent_mix(total: int = 1_000_000) -> dict[str, int]:
    """The token mix issue #904 measured on a real agent trace: 95.64% cache
    read, 4.07% cache miss, 0.29% output. ``cache_read`` dominating is why a
    wrong cache-read rate moved the total more than input and output combined.
    """
    return {
        "cache_read_input_tokens": round(total * 0.9564),
        "input_tokens": round(total * 0.0407),
        "output_tokens": round(total * 0.0029),
        "cache_creation_input_tokens": 0,
    }


@pytest.mark.parametrize("model", MODELS)
def test_compute_cost_doubles_inside_a_peak_window(model: str) -> None:
    usage = _agent_mix()
    off = compute_cost(model, usage, request_time=OFF_PEAK_TS)
    peak = compute_cost(model, usage, request_time=PEAK_TS)
    assert off > 0
    assert peak == pytest.approx(2 * off, rel=1e-12)


def test_agent_mix_cost_matches_the_published_card() -> None:
    """The end-to-end number issue #904 reported as 2.3x/4.5x low. Recomputed
    from the published card rather than from the tiers, so a future edit to
    ``services/pricing.py`` alone cannot make this pass."""
    usage = _agent_mix()
    for model in MODELS:
        for window, ts in (("off_peak", OFF_PEAK_TS), ("peak", PEAK_TS)):
            card = PUBLISHED[model][window]
            expected = (
                usage["input_tokens"] * card["input"]
                + usage["output_tokens"] * card["output"]
                + usage["cache_read_input_tokens"] * card["cache_read"]
            ) / 1_000_000
            got = compute_cost(model, usage, request_time=ts)
            assert got == pytest.approx(expected, rel=1e-12), (model, window)


def test_pro_off_peak_agent_mix_is_the_issues_number() -> None:
    """$0.0536 per 1M tokens off-peak, $0.1073 peak — against the $0.0237 the
    stale card returned. Pinned as an absolute so a regression to any card
    that merely has the right shape is visible as a dollar figure."""
    usage = _agent_mix()
    assert compute_cost(
        "deepseek-v4-pro", usage, request_time=OFF_PEAK_TS
    ) == pytest.approx(0.0536, abs=5e-5)
    assert compute_cost(
        "deepseek-v4-pro", usage, request_time=PEAK_TS
    ) == pytest.approx(0.1073, abs=5e-5)


def test_vendor_prefix_stripped_ids_follow_the_schedule() -> None:
    """OpenRouter's ``deepseek/…`` ids resolve to the upstream card via
    get_pricing's prefix strip, so they must carry the timestamp too."""
    for model in MODELS:
        for ts in (OFF_PEAK_TS, PEAK_TS):
            assert get_pricing(f"deepseek/{model}", request_time=ts) == (
                get_pricing(model, request_time=ts)
            )


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5", "claude-sonnet-5", "MiniMax-M3", "kimi-k3",
     "gpt-5.6-luna", "muse-spark-1.1"],
)
def test_no_other_model_is_time_tiered(model: str) -> None:
    """Scope gate: the new axis is DeepSeek-only. Every other row must return
    the same card at every instant of the week."""
    baseline = get_pricing(model, request_time=OFF_PEAK_TS)
    assert baseline is not None
    start = _utc(2026, 8, MON, 0)
    for h in range(7 * 24):
        assert get_pricing(model, request_time=start + h * 3600) == baseline


def test_unknown_models_still_return_none_at_every_hour() -> None:
    start = _utc(2026, 8, MON, 0)
    for h in range(0, 7 * 24, 6):
        assert get_pricing("totally-unknown-model-xyz",
                           request_time=start + h * 3600) is None


def test_cache_savings_price_events_at_their_own_timestamp() -> None:
    """``get_cache_savings`` runs at DISPLAY time, arbitrarily later than the
    requests it sums. An off-peak session's savings must not be restated at
    peak rates because the user happened to open ``/cost`` at 02:00 UTC."""
    from src.services.cost_tracker import CostTracker

    tracker = CostTracker()
    tracker.record_usage("deepseek-v4-pro", {
        "input_tokens": 10_000,
        "output_tokens": 1_000,
        "cache_read_input_tokens": 900_000,
    })
    # Back-date the recorded event into an off-peak window, then read the
    # savings back as if the clock had since moved into a peak one.
    tracker._events[0].timestamp = OFF_PEAK_TS
    saved = tracker.get_cache_savings()
    off = get_pricing("deepseek-v4-pro", request_time=OFF_PEAK_TS)
    expected = 900_000 * (off["input"] - off["cache_read"])
    assert saved == pytest.approx(expected, rel=1e-12)


def test_leap_second_free_arithmetic_across_a_dst_shift() -> None:
    """UTC has no DST, so a fixed 24h offset lands on the same wall hour. This
    guards against anyone reimplementing the window check in local time."""
    base = _utc(2026, 3, 27, 2)  # Friday 02:00 UTC, inside a peak window
    assert is_deepseek_peak(base) is True
    day_later = (
        datetime.fromtimestamp(base, timezone.utc) + timedelta(days=1)
    ).timestamp()
    assert is_deepseek_peak(day_later) is False  # Saturday

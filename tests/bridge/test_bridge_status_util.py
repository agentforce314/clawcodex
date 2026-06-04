"""Tests for ``src.bridge.bridge_status_util``."""

from __future__ import annotations

import re

import pytest

from src.bridge.bridge_status_util import (
    FAILED_FOOTER_TEXT,
    SHIMMER_INTERVAL_MS,
    TOOL_DISPLAY_EXPIRY_MS,
    abbreviate_activity,
    build_active_footer_text,
    build_bridge_connect_url,
    build_bridge_session_url,
    build_idle_footer_text,
    compute_glimmer_index,
    compute_shimmer_segments,
    format_duration,
    get_bridge_status,
    get_claude_ai_base_url,
    get_remote_session_url,
    string_width,
    timestamp,
    truncate_to_width,
    wrap_with_osc8_link,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_tool_display_expiry_ms_matches_ts() -> None:
    assert TOOL_DISPLAY_EXPIRY_MS == 30_000


def test_shimmer_interval_ms_matches_ts() -> None:
    assert SHIMMER_INTERVAL_MS == 150


def test_failed_footer_text_matches_ts() -> None:
    assert FAILED_FOOTER_TEXT == 'Something went wrong, please try again'


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('ms', 'expected'),
    [
        # ``format_duration`` is the canonical ``src.utils.format`` helper —
        # see module docstring "Behavior note". It uses ``floor`` (not
        # JS round-half-up) for the seconds-only branch and adds hours/
        # days for ms >= 1h, which diverges from TS ``bridgeStatusUtil``'s
        # re-export of TS ``utils/format``. Tests pin the actual behavior
        # of the canonical Python helper, not the TS-exact bridge variant.
        (0, '0s'),
        (500, '0s'),        # floor(0.5) = 0
        (1_499, '1s'),
        (1_500, '1s'),      # floor(1.5) = 1 (not Math.round → 2)
        (59_999, '59s'),    # floor(59.999) = 59
        (60_000, '1m 0s'),  # hide_trailing_zeros=False default
        (60_500, '1m 1s'),
        (75_000, '1m 15s'),
        # utils/format adds hours branch (TS bridge has none).
        (3_600_000, '1h 0m 0s'),
        (3_660_000, '1h 1m 0s'),
    ],
)
def test_format_duration(ms: int, expected: str) -> None:
    assert format_duration(ms) == expected


def test_format_duration_negative_uses_decimal_branch() -> None:
    """Negative ms hits the ``ms < 1`` decimal branch in utils/format.

    Documents the divergence: ``utils/format`` returns ``"-0.5s"``;
    TS bridge would render ``"0s"`` (Math.round(-0.5/1000) == 0). The
    bridge UI never passes negative durations in practice (elapsed times
    are non-negative by construction).
    """
    assert format_duration(-500) == '-0.5s'


# ---------------------------------------------------------------------------
# string_width + truncate_to_width
# ---------------------------------------------------------------------------


def test_string_width_ascii() -> None:
    assert string_width('hello') == 5


def test_string_width_empty() -> None:
    assert string_width('') == 0


def test_string_width_cjk_is_double() -> None:
    """CJK characters are 2 cols wide each."""
    assert string_width('你好') == 4


def test_string_width_strips_ansi() -> None:
    """ANSI escape codes shouldn't count toward visual width."""
    s = '\x1b[31mred\x1b[0m'
    assert string_width(s) == 3


def test_truncate_to_width_short_string_unchanged() -> None:
    assert truncate_to_width('hi', 10) == 'hi'


def test_truncate_to_width_at_boundary_unchanged() -> None:
    assert truncate_to_width('hello', 5) == 'hello'


def test_truncate_to_width_truncates_with_ellipsis() -> None:
    out = truncate_to_width('hello world', 6)
    assert out == 'hello…'
    assert string_width(out) == 6


def test_truncate_to_width_zero_returns_empty() -> None:
    assert truncate_to_width('hello', 0) == ''


def test_truncate_to_width_one_returns_ellipsis_only() -> None:
    assert truncate_to_width('hello', 1) == '…'


def test_truncate_to_width_cjk_grapheme_aware() -> None:
    """CJK characters aren't split mid-codepoint."""
    out = truncate_to_width('你好世界', 5)
    # '你' = 2 cols, ellipsis = 1 col; budget 4 fits one '你', another
    # '好' would push to 4 then ellipsis pushes to 5 — should be '你好…'.
    assert out == '你好…'
    assert string_width(out) == 5


def test_truncate_to_width_cjk_overflow_returns_ellipsis_only() -> None:
    """If budget is too small to fit any CJK character, return just '…'.

    Regression test pinned per CRITIC feedback — locks current behavior:
    width budget 2 minus ellipsis 1 = 1 col for content, but a CJK char
    is 2 cols so nothing fits → output is just the ellipsis (1 col).
    """
    out = truncate_to_width('你你', 2)
    assert out == '…'
    assert string_width(out) == 1


def test_truncate_to_width_preserves_ansi_escapes_whole() -> None:
    """ANSI SGR sequences are zero-width and never split mid-escape.

    Regression test per CRITIC feedback. Before the fix, the grapheme
    splitter would chop ``\\x1b[31m`` into 5 separate "graphemes" each
    counted as 1 col by ``string_width``, producing partial escapes that
    bleed color into the terminal.
    """
    s = '\x1b[31mhello\x1b[0m world'
    out = truncate_to_width(s, 6)
    # The escape sequences are zero-width and kept whole; visible
    # content trims at 5 cols ('hello'), then ellipsis.
    # Order must be: red-open, "hello", red-close, ellipsis.
    assert '\x1b[31m' in out  # opening SGR intact
    assert '\x1b[0m' in out   # closing SGR intact
    assert 'hello' in out
    assert out.endswith('…')
    # Visible width must equal max_width.
    assert string_width(out) == 6


def test_truncate_to_width_with_ansi_mid_truncation() -> None:
    """ANSI escapes inside a truncated region are still preserved."""
    # 'a\x1b[31mbc' = a(1) + ansi(0) + b(1) + c(1) = 3 cols visible.
    # truncate to 2 cols: budget 1 + ellipsis 1 → only 'a' + ansi + '…'.
    s = 'a\x1b[31mbc'
    out = truncate_to_width(s, 2)
    # 'a', then the ansi (zero-width, always emitted), then '…'.
    assert out == 'a\x1b[31m…'
    assert string_width(out) == 2


def test_truncate_to_width_preserves_ansi_closer_after_break() -> None:
    """A trailing ``\\x1b[0m`` reset in the tail still gets emitted.

    Regression test per CRITIC follow-up: previously the loop ``break``'d
    on the first over-budget visible token and dropped any post-break
    zero-width tokens. That would leave colored output bleeding into
    everything written after the truncated string. Now we keep iterating
    and emit only the zero-width tokens.
    """
    s = 'a\x1b[31mb\x1b[0mc'
    out = truncate_to_width(s, 2)
    # Budget 1 visible col + 1 ellipsis col. 'a' fits, opening SGR
    # zero-width emitted, 'b' over budget, closing SGR zero-width emitted,
    # 'c' dropped, then ellipsis.
    assert '\x1b[31m' in out
    assert '\x1b[0m' in out  # closer preserved
    assert 'a' in out
    assert out.endswith('…')
    assert string_width(out) == 2


def test_truncate_to_width_osc8_link_treated_as_zero_width() -> None:
    """OSC 8 hyperlinks (\\x1b]8;;url\\x07text\\x1b]8;;\\x07) preserved.

    The two OSC 8 markers each contribute 0 visual width; only the text
    in between is measured for truncation.
    """
    s = '\x1b]8;;https://x.com\x07link\x1b]8;;\x07 tail'
    # Visible width = 4 (link) + 1 (space) + 4 (tail) = 9.
    assert string_width(s) == 9
    out = truncate_to_width(s, 5)
    # OSC 8 markers preserved, 'link' fits exactly, then ellipsis.
    assert '\x1b]8;;https://x.com\x07' in out
    assert '\x1b]8;;\x07' in out
    assert out.endswith('…')
    assert string_width(out) == 5


# ---------------------------------------------------------------------------
# timestamp + abbreviate_activity
# ---------------------------------------------------------------------------


def test_timestamp_format() -> None:
    out = timestamp()
    assert re.match(r'^\d{2}:\d{2}:\d{2}$', out) is not None


def test_abbreviate_activity_short_unchanged() -> None:
    assert abbreviate_activity('hi') == 'hi'


def test_abbreviate_activity_long_truncated_to_30_cols() -> None:
    long = 'Reading ' + 'a' * 100
    out = abbreviate_activity(long)
    assert string_width(out) == 30
    assert out.endswith('…')


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def test_get_claude_ai_base_url_production() -> None:
    assert get_claude_ai_base_url() == 'https://claude.ai'


def test_get_claude_ai_base_url_ingress_param_ignored() -> None:
    """``ingress_url`` is a no-op until constants/product.py is ported."""
    assert get_claude_ai_base_url('https://staging.example.com') == 'https://claude.ai'


def test_get_remote_session_url_retags_cse_to_session() -> None:
    out = get_remote_session_url('cse_abc123')
    assert out == 'https://claude.ai/code/session_abc123'


def test_get_remote_session_url_idempotent_on_session_prefix() -> None:
    out = get_remote_session_url('session_xyz')
    assert out == 'https://claude.ai/code/session_xyz'


def test_build_bridge_connect_url() -> None:
    out = build_bridge_connect_url('env-1')
    assert out == 'https://claude.ai/code?bridge=env-1'


def test_build_bridge_session_url_includes_bridge_param() -> None:
    out = build_bridge_session_url('cse_abc', 'env-1')
    assert out == 'https://claude.ai/code/session_abc?bridge=env-1'


# ---------------------------------------------------------------------------
# Shimmer math
# ---------------------------------------------------------------------------


def test_compute_glimmer_index_decreases_with_tick() -> None:
    """At tick=0 index is message_width+10; at tick=1 it's message_width+9."""
    width = 20
    assert compute_glimmer_index(0, width) == 30
    assert compute_glimmer_index(1, width) == 29


def test_compute_glimmer_index_cycles() -> None:
    """Cycle length is message_width+20."""
    width = 20
    cycle = width + 20
    assert compute_glimmer_index(0, width) == compute_glimmer_index(cycle, width)


def test_compute_shimmer_segments_offscreen_left_returns_all_in_before() -> None:
    text = 'hello'
    out = compute_shimmer_segments(text, glimmer_index=10)
    # message_width=5, shimmer_start=9 >= 5 → offscreen right, all before.
    assert out.before == 'hello'
    assert out.shimmer == ''
    assert out.after == ''


def test_compute_shimmer_segments_offscreen_right_returns_all_in_before() -> None:
    text = 'hello'
    out = compute_shimmer_segments(text, glimmer_index=-10)
    # shimmer_end=-9 < 0 → offscreen left, all before per TS.
    assert out.before == 'hello'
    assert out.shimmer == ''
    assert out.after == ''


def test_compute_shimmer_segments_splits_at_index() -> None:
    text = 'abcdefghij'
    out = compute_shimmer_segments(text, glimmer_index=5)
    # shimmer_start=4 shimmer_end=6 → cols [4,6] inclusive in shimmer.
    # before = cols 0-3 = 'abcd'; shimmer = 'efg'; after = 'hij'.
    assert out.before == 'abcd'
    assert out.shimmer == 'efg'
    assert out.after == 'hij'


def test_compute_shimmer_segments_preserves_total_text() -> None:
    """before + shimmer + after must reconstruct the original text."""
    text = 'the quick brown fox'
    for idx in range(-5, 30):
        out = compute_shimmer_segments(text, glimmer_index=idx)
        assert out.before + out.shimmer + out.after == text


# ---------------------------------------------------------------------------
# get_bridge_status
# ---------------------------------------------------------------------------


def test_get_bridge_status_error_wins() -> None:
    """Error takes precedence over connected/session_active/reconnecting."""
    info = get_bridge_status(
        error='boom', connected=True, session_active=True, reconnecting=True
    )
    assert info.label == 'Remote Control failed'
    assert info.color == 'error'


def test_get_bridge_status_reconnecting_when_no_error() -> None:
    info = get_bridge_status(
        error=None, connected=False, session_active=False, reconnecting=True
    )
    assert info.label == 'Remote Control reconnecting'
    assert info.color == 'warning'


def test_get_bridge_status_active_when_connected() -> None:
    info = get_bridge_status(
        error=None, connected=True, session_active=False, reconnecting=False
    )
    assert info.label == 'Remote Control active'
    assert info.color == 'success'


def test_get_bridge_status_active_when_session_active() -> None:
    info = get_bridge_status(
        error=None, connected=False, session_active=True, reconnecting=False
    )
    assert info.label == 'Remote Control active'
    assert info.color == 'success'


def test_get_bridge_status_connecting_when_idle() -> None:
    info = get_bridge_status(
        error=None, connected=False, session_active=False, reconnecting=False
    )
    assert info.label.startswith('Remote Control connecting')
    assert info.color == 'warning'


# ---------------------------------------------------------------------------
# Footer + OSC 8
# ---------------------------------------------------------------------------


def test_build_idle_footer_text() -> None:
    out = build_idle_footer_text('https://claude.ai/code?bridge=env-1')
    assert 'Code everywhere with the Claude app' in out
    assert 'https://claude.ai/code?bridge=env-1' in out


def test_build_active_footer_text() -> None:
    out = build_active_footer_text('https://claude.ai/code/session_x')
    assert 'Continue coding in the Claude app' in out
    assert 'https://claude.ai/code/session_x' in out


def test_wrap_with_osc8_link_zero_visual_width() -> None:
    """OSC 8 hyperlink markers have zero visual width per terminal spec."""
    visible = 'click here'
    wrapped = wrap_with_osc8_link(visible, 'https://example.com')
    assert visible in wrapped
    assert '\x1b]8;;https://example.com\x07' in wrapped
    assert '\x1b]8;;\x07' in wrapped
    assert string_width(wrapped) == string_width(visible)

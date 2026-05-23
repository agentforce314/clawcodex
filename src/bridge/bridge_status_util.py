"""Status-line formatting + URL helpers for the bridge UI.

Ports ``typescript/src/bridge/bridgeStatusUtil.ts``.

The TS file pulls from four upstream modules:

* ``utils/format.formatDuration`` + ``truncateToWidth``
* ``constants/product.getClaudeAiBaseUrl`` + ``getRemoteSessionUrl``
* ``ink/stringWidth.stringWidth``
* ``utils/intl.getGraphemeSegmenter``

This module:

* **Imports** ``format_duration`` from ``src.utils.format`` (already
  ported, more sophisticated than what TS bridge needs — see "Behavior
  note" below).
* **Inlines** ``truncate_to_width``, ``string_width``, the grapheme
  segmenter, and the claude.ai URL helpers per refactoring plan §2 item
  13. ``wcwidth`` substitutes for ``stringWidth`` (handles CJK + emoji
  width correctly), and the ``regex`` package's ``\\X`` grapheme pattern
  substitutes for ``Intl.Segmenter``. The URL builders are inlined as
  production-only constants/functions until ``constants/product.py`` is
  ported.

**Behavior note on ``format_duration``**: TS ``utils/format.ts:formatDuration``
has no hours/days branch — anything ≥1h renders as ``"60m"``, ``"120m"``,
etc. The Python ``src.utils.format.format_duration`` extends this with
days/hours and ``hide_trailing_zeros`` / ``most_significant_only``
flags. We re-export it unmodified; bridge UI sites that need TS-exact
output should be aware of the divergence (mostly cosmetic — affects only
the very long-duration case which the bridge rarely hits). Trailing-zero
seconds are not hidden by default (e.g. ``"1m 0s"`` not ``"1m"``); pass
``hide_trailing_zeros=True`` to match TS bridge exactly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import regex
import wcwidth

# Re-export the canonical format_duration so callers can write
# ``from src.bridge.bridge_status_util import format_duration``. See
# module docstring "Behavior note" for the divergence vs TS bridge.
from src.utils.format import format_duration  # noqa: F401  re-export

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

StatusState = Literal['idle', 'attached', 'titled', 'reconnecting', 'failed']
"""Mirrors TS ``StatusState`` on ``bridgeStatusUtil.ts:10-15``."""

TOOL_DISPLAY_EXPIRY_MS: int = 30_000
"""How long a tool activity line stays visible after last tool_start (ms).

Mirrors TS ``TOOL_DISPLAY_EXPIRY_MS`` on ``bridgeStatusUtil.ts:18``.
"""

SHIMMER_INTERVAL_MS: int = 150
"""Interval for the shimmer animation tick (ms).

Mirrors TS ``SHIMMER_INTERVAL_MS`` on ``bridgeStatusUtil.ts:21``.
"""

FAILED_FOOTER_TEXT: str = 'Something went wrong, please try again'
"""Footer text shown when the bridge has failed.

Mirrors TS ``FAILED_FOOTER_TEXT`` on ``bridgeStatusUtil.ts:154``.
"""


_CLAUDE_AI_PRODUCTION_BASE: str = 'https://claude.ai'
"""Production claude.ai base URL.

Inlined substitute for TS ``getClaudeAiBaseUrl()`` happy-path return.
Staging / FedStart variants land when ``constants/product.py`` is ported.
"""


# ---------------------------------------------------------------------------
# Inlined utility helpers (substitute for cross-folder TS imports)
# ---------------------------------------------------------------------------


def string_width(s: str) -> int:
    """Visual width of a string in terminal columns.

    Inlined substitute for TS ``ink/stringWidth``. Uses ``wcwidth`` which
    correctly handles CJK (2 cols), emoji (typically 2 cols), and ANSI
    escape sequences (0 cols).
    """
    if not s:
        return 0
    # ``wcwidth.wcswidth`` returns -1 if the string contains a control
    # character that has no defined width. Treat those as 0 to match the
    # TS string-width behavior (which strips control chars before measuring).
    w = wcwidth.wcswidth(_strip_ansi(s))
    return max(w, 0)


def truncate_to_width(s: str, max_width: int) -> str:
    """Truncate a string to ``max_width`` visual columns with ellipsis.

    Inlined substitute for TS ``utils/format.truncateToWidth``. Appends
    ``'…'`` (1 col) when truncation occurs; if ``max_width`` is too small
    to fit even the ellipsis, returns the empty string. Grapheme-aware so
    multi-byte chars don't get split in the middle.

    **ANSI-aware**: SGR escape sequences (``\\x1b[...m``) and OSC 8
    hyperlink markers contribute zero visual width and are kept whole —
    they will never be split mid-escape, even when truncation lands on
    a colored substring. After truncation, all open SGR groups are
    implicitly closed by the upstream renderer's ``\\x1b[0m`` reset (the
    bridge UI always appends one); callers that don't do this should
    append ``'\\x1b[0m'`` to the result to avoid color bleed.
    """
    if max_width <= 0:
        return ''
    if string_width(s) <= max_width:
        return s
    if max_width == 1:
        return '…'
    # Walk the string as a sequence of (token, visible_width) pairs where
    # token is either an ANSI escape (width 0, kept whole) or a Unicode
    # grapheme (width per wcwidth). Accumulate visible tokens until
    # adding the next would exceed budget = max_width - 1, then switch
    # to "truncated" mode where we keep emitting zero-width tokens only
    # so trailing ``\x1b[0m`` resets (and any other escapes in the tail)
    # survive — preventing color bleed into terminal output that follows.
    budget = max_width - 1
    out: list[str] = []
    acc = 0
    truncated = False
    for token, width in _iter_tokens(s):
        if width == 0:
            out.append(token)
            continue
        if truncated:
            continue  # post-break visible content is dropped
        if acc + width > budget:
            truncated = True
            continue
        out.append(token)
        acc += width
    return ''.join(out) + '…'


# Backwards-compat alias matching TS export name ``truncatePrompt``.
truncate_prompt = truncate_to_width


_ANSI_RE = regex.compile(r'\x1b\[[0-9;]*m|\x1b\]8;;[^\x07]*\x07')


def _strip_ansi(s: str) -> str:
    """Strip ANSI SGR + OSC 8 hyperlink sequences for width calculation."""
    return _ANSI_RE.sub('', s)


def _grapheme_split(s: str) -> list[str]:
    """Split a string into Unicode graphemes.

    Substitute for TS ``Intl.Segmenter``. Uses the ``regex`` package's
    ``\\X`` (extended grapheme cluster) pattern, which is the canonical
    Unicode definition.
    """
    return regex.findall(r'\X', s)


def _iter_tokens(s: str):
    """Iterate ``(token, visible_width)`` pairs preserving ANSI escapes.

    Yields ANSI SGR / OSC 8 sequences as zero-width whole tokens so
    ``truncate_to_width`` never splits them mid-escape. Non-escape spans
    are grapheme-split via ``\\X`` and yielded one cluster at a time with
    their ``wcwidth`` value.
    """
    pos = 0
    for match in _ANSI_RE.finditer(s):
        if match.start() > pos:
            for grapheme in regex.findall(r'\X', s[pos:match.start()]):
                yield grapheme, max(wcwidth.wcswidth(grapheme), 0)
        yield match.group(0), 0
        pos = match.end()
    if pos < len(s):
        for grapheme in regex.findall(r'\X', s[pos:]):
            yield grapheme, max(wcwidth.wcswidth(grapheme), 0)


def get_claude_ai_base_url(ingress_url: str | None = None) -> str:
    """Base URL for claude.ai links shown in the terminal UI.

    Inlined substitute for TS ``constants/product.getClaudeAiBaseUrl``.
    The TS version derives staging/FedStart variants from ``ingress_url``;
    this Python port returns the production URL unconditionally until
    ``constants/product.py`` is ported.
    """
    return _CLAUDE_AI_PRODUCTION_BASE


def get_remote_session_url(
    session_id: str,
    ingress_url: str | None = None,
) -> str:
    """Build the claude.ai/code URL for an attached session.

    Inlined substitute for TS ``constants/product.getRemoteSessionUrl``.
    Performs the same ``cse_`` → ``session_`` retag the TS version does
    (so the URL is browser-routable through the v1 compat layer).
    """
    if session_id.startswith('cse_'):
        compat_id = 'session_' + session_id[len('cse_'):]
    else:
        compat_id = session_id
    return f'{get_claude_ai_base_url(ingress_url)}/code/{compat_id}'


# ---------------------------------------------------------------------------
# Public timestamp / formatting helpers
# ---------------------------------------------------------------------------


def timestamp() -> str:
    """Format current local time as ``HH:MM:SS``.

    Mirrors TS ``timestamp`` on ``bridgeStatusUtil.ts:23-29``.
    """
    now = time.localtime()
    return f'{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}'


def abbreviate_activity(summary: str) -> str:
    """Abbreviate a tool activity summary for the trail display.

    Mirrors TS ``abbreviateActivity`` on ``bridgeStatusUtil.ts:34-36``.
    """
    return truncate_to_width(summary, 30)


# ---------------------------------------------------------------------------
# URL builders for the bridge
# ---------------------------------------------------------------------------


def build_bridge_connect_url(
    environment_id: str,
    ingress_url: str | None = None,
) -> str:
    """URL shown when the bridge is idle (waiting for a session).

    Mirrors TS ``buildBridgeConnectUrl`` on ``bridgeStatusUtil.ts:39-45``.
    """
    base = get_claude_ai_base_url(ingress_url)
    return f'{base}/code?bridge={environment_id}'


def build_bridge_session_url(
    session_id: str,
    environment_id: str,
    ingress_url: str | None = None,
) -> str:
    """URL shown when a session is attached.

    Mirrors TS ``buildBridgeSessionUrl`` on ``bridgeStatusUtil.ts:52-58``.
    Delegates to ``get_remote_session_url`` for the cse_→session_ retag,
    then appends the v1-specific ``?bridge={environment_id}`` query.
    """
    base = get_remote_session_url(session_id, ingress_url)
    return f'{base}?bridge={environment_id}'


# ---------------------------------------------------------------------------
# Shimmer animation math
# ---------------------------------------------------------------------------


def compute_glimmer_index(tick: int, message_width: int) -> int:
    """Compute the column index for a reverse-sweep shimmer animation.

    Mirrors TS ``computeGlimmerIndex`` on ``bridgeStatusUtil.ts:61-67``.
    Walks right-to-left, off-screen-padded by 10 cols on each side so the
    shimmer doesn't visually stick at the edges.
    """
    cycle_length = message_width + 20
    return message_width + 10 - (tick % cycle_length)


@dataclass(frozen=True)
class ShimmerSegments:
    """Three-segment split of a string by visual column position.

    Mirrors TS ``{before, shimmer, after}`` return on
    ``bridgeStatusUtil.ts:82``.
    """

    before: str
    shimmer: str
    after: str


def compute_shimmer_segments(text: str, glimmer_index: int) -> ShimmerSegments:
    """Split text into three segments by visual column position.

    Mirrors TS ``computeShimmerSegments`` on ``bridgeStatusUtil.ts:79-111``.
    Multi-byte chars + emoji + CJK are kept whole; the split is by
    cumulative column position, not codepoint count.

    Returns ``before``, ``shimmer`` (≤ 3 cols centered on
    ``glimmer_index``), and ``after``. When the shimmer is offscreen,
    everything goes into ``before``.
    """
    message_width = string_width(text)
    shimmer_start = glimmer_index - 1
    shimmer_end = glimmer_index + 1

    if shimmer_start >= message_width or shimmer_end < 0:
        return ShimmerSegments(before=text, shimmer='', after='')

    clamped_start = max(0, shimmer_start)
    col_pos = 0
    before = []
    shimmer = []
    after = []
    for grapheme in _grapheme_split(text):
        seg_width = string_width(grapheme)
        if col_pos + seg_width <= clamped_start:
            before.append(grapheme)
        elif col_pos > shimmer_end:
            after.append(grapheme)
        else:
            shimmer.append(grapheme)
        col_pos += seg_width

    return ShimmerSegments(
        before=''.join(before),
        shimmer=''.join(shimmer),
        after=''.join(after),
    )


# ---------------------------------------------------------------------------
# Status state machine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeStatusInfo:
    """Computed bridge status label and color from connection state.

    Mirrors TS ``BridgeStatusInfo`` on ``bridgeStatusUtil.ts:115-121``.
    """

    label: str
    color: Literal['error', 'warning', 'success']


def get_bridge_status(
    *,
    error: str | None,
    connected: bool,
    session_active: bool,
    reconnecting: bool,
) -> BridgeStatusInfo:
    """Derive a status label + color from the bridge connection state.

    Mirrors TS ``getBridgeStatus`` on ``bridgeStatusUtil.ts:124-141``.
    Keyword-only args to make call sites self-documenting (TS uses an
    object literal; Python uses kw-only).
    """
    if error:
        return BridgeStatusInfo(label='Remote Control failed', color='error')
    if reconnecting:
        return BridgeStatusInfo(
            label='Remote Control reconnecting', color='warning'
        )
    if session_active or connected:
        return BridgeStatusInfo(
            label='Remote Control active', color='success'
        )
    return BridgeStatusInfo(
        label='Remote Control connecting…', color='warning'
    )


# ---------------------------------------------------------------------------
# Footer text builders
# ---------------------------------------------------------------------------


def build_idle_footer_text(url: str) -> str:
    """Footer text shown when bridge is idle (Ready state).

    Mirrors TS ``buildIdleFooterText`` on ``bridgeStatusUtil.ts:144-146``.
    """
    return f'Code everywhere with the Claude app or {url}'


def build_active_footer_text(url: str) -> str:
    """Footer text shown when a session is active (Connected state).

    Mirrors TS ``buildActiveFooterText`` on ``bridgeStatusUtil.ts:149-151``.
    """
    return f'Continue coding in the Claude app or {url}'


# ---------------------------------------------------------------------------
# Terminal hyperlink wrapping
# ---------------------------------------------------------------------------


def wrap_with_osc8_link(text: str, url: str) -> str:
    """Wrap text in an OSC 8 terminal hyperlink. Zero visual width.

    Mirrors TS ``wrapWithOsc8Link`` on ``bridgeStatusUtil.ts:161-163``.
    Strippable by ``_strip_ansi`` so ``string_width`` returns the visible
    text width only.
    """
    return f'\x1b]8;;{url}\x07{text}\x1b]8;;\x07'


__all__ = [
    'BridgeStatusInfo',
    'FAILED_FOOTER_TEXT',
    'SHIMMER_INTERVAL_MS',
    'ShimmerSegments',
    'StatusState',
    'TOOL_DISPLAY_EXPIRY_MS',
    'abbreviate_activity',
    'build_active_footer_text',
    'build_bridge_connect_url',
    'build_bridge_session_url',
    'build_idle_footer_text',
    'compute_glimmer_index',
    'compute_shimmer_segments',
    'format_duration',
    'get_bridge_status',
    'get_claude_ai_base_url',
    'get_remote_session_url',
    'string_width',
    'timestamp',
    'truncate_prompt',
    'truncate_to_width',
    'wrap_with_osc8_link',
]

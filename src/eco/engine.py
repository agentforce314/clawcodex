"""Dispatch engine: raw Bash output → compressed model-bound rendering.

Order of operations per command (RTK runner/emit_guarded ported to our
harness position):

1. Normalize the *full* pre-truncation output (``\\r`` frames, ANSI) — this
   processed text is both the filter input and the tee payload, so
   ``tail -n +N`` hints line up exactly with what the filter counted.
2. First matching filter wins; no match → ``None`` (passthrough — the caller
   ships its baseline untouched and nothing is recorded).
3. Loss accounting: ``safe_loss`` hits (pure-ceremony strips) ship as-is;
   every other hit must tee the processed text and append a recovery hint —
   tee unavailable → the hit is DISCARDED (RTK's never-lossy-without-recovery
   rule, main.rs:1341).
4. ``never_worse`` against the exact baseline the mapper would otherwise
   emit; baseline wins → passthrough.
5. Record savings (only real compressions — passthroughs never dilute stats).

Any exception → passthrough (a compressor must never break the tool).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .filters import FILTERS, normalize_carriage_returns, strip_ansi
from .guard import estimate_tokens, never_worse
from .state import record_compression
from .tee import full_hint, tail_hint, tee_raw

logger = logging.getLogger(__name__)

# A "compressed" rendering larger than this is a filter bug; the existing
# bash truncate_output contract stays intact above us.
_MAX_ECO_CHARS = 30_000

# Inputs beyond this skip eco entirely (RTK RAW_CAP spirit): the regex passes
# would be pure cost, and the Step-11 <persisted-output> layer already gives
# giant results a preview + full-file pointer.
_MAX_INPUT_CHARS = 10_485_760

# Conservative allowance for a recovery-hint line when pre-checking the guard
# before writing the tee file (paths are ~60-120 chars → ~30 tokens).
_HINT_TOKEN_ALLOWANCE = 30


@dataclass(frozen=True)
class EcoOutcome:
    content: str
    filter_name: str
    baseline_tokens: int
    eco_tokens: int

    @property
    def saved_tokens(self) -> int:
        return max(0, self.baseline_tokens - self.eco_tokens)


def _slug_for(command: str) -> str:
    return "_".join(command.strip().split())[:40] or "cmd"


def compress_bash_output(
    command: str,
    exit_code: int,
    full_text: str,
    baseline: str,
    tee_dir: Path | None,
) -> EcoOutcome | None:
    """Compress one Bash result; ``None`` means "ship the baseline".

    ``full_text`` is the pre-truncation stdout+stderr assembly (the mapper's
    shape, but unbounded); ``baseline`` is exactly what the mapper would emit
    without eco. ``tee_dir`` is the per-session eco directory (None → only
    safe-loss filters can fire).
    """
    try:
        if not baseline.strip():
            return None
        if len(full_text) > _MAX_INPUT_CHARS:
            return None

        processed = strip_ansi(normalize_carriage_returns(full_text))
        baseline_tokens = estimate_tokens(baseline)

        for filt in FILTERS:
            try:
                hit = filt(command, exit_code, processed)
            except Exception:  # noqa: BLE001 — one bad filter must not break the chain
                logger.debug("[eco] filter %r failed", filt, exc_info=True)
                continue
            if hit is None:
                continue
            if not hit.body.strip():
                # Filters must never produce empty output (the downstream
                # empty-content marker would misreport "no output").
                continue

            body = hit.body
            if not hit.safe_loss:
                if tee_dir is None:
                    continue
                # Guard pre-check BEFORE writing: if the body plus a typical
                # hint can't beat the baseline, don't leave an orphan file.
                if (
                    estimate_tokens(body) + _HINT_TOKEN_ALLOWANCE
                    > baseline_tokens
                ):
                    continue
                path = tee_raw(processed, _slug_for(command), tee_dir)
                if path is None:
                    # Tiny content (< MIN_TEE_SIZE) or write failure: loss
                    # would be unrecoverable → discard the hit.
                    continue
                hint = (
                    tail_hint(path, hit.tail_offset)
                    if hit.tail_offset is not None
                    else full_hint(path)
                )
                body = f"{body}\n{hint}"
                if (
                    len(body) > _MAX_ECO_CHARS
                    or never_worse(baseline, body) == baseline
                ):
                    # The real hint pushed it over after all — remove the
                    # now-unreferenced tee file and pass through.
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    continue
            else:
                if len(body) > _MAX_ECO_CHARS or never_worse(baseline, body) == baseline:
                    continue

            eco_tokens = estimate_tokens(body)
            record_compression(hit.name, baseline_tokens, eco_tokens)
            return EcoOutcome(
                content=body,
                filter_name=hit.name,
                baseline_tokens=baseline_tokens,
                eco_tokens=eco_tokens,
            )
        return None
    except Exception:  # noqa: BLE001 — the engine must never break the Bash tool
        logger.debug("[eco] engine failed; passing through", exc_info=True)
        return None

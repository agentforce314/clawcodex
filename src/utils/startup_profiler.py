"""Startup profiling instrumentation — gap #6 from ch17.

Records named checkpoints with monotonic timestamps so cold-start latency
sources can be attributed to specific phases. Mirrors TS
``utils/startupProfiler.ts:65,123`` (``profileCheckpoint`` /
``profileReport``) but emits to disk + stderr instead of Statsig (sampling
is a follow-on).

Usage::

    from src.utils.startup_profiler import profile_checkpoint
    profile_checkpoint("cli_main_entry")

The function is a near-zero-cost no-op when ``CLAUDE_CODE_PROFILE_STARTUP``
is unset; that gate is what makes call sites safe to scatter through the
critical path. When the env var is truthy at process start, an ``atexit``
handler writes the report to ``.clawcodex/startup-perf/{session_id}.txt`` and
emits a one-line stderr summary.

The chapter's thesis (line 205): *"Measurement first, optimization second,
always."* This module is what makes the rest of the ch17 plan data-driven.
"""

from __future__ import annotations

import atexit
import os
import sys
import time
import uuid
from pathlib import Path

__all__ = [
    "is_profiling_enabled",
    "profile_checkpoint",
    "profile_report",
    "get_internal_phase_log",
    "reset_profiler_for_test_only",
]


# Storage. List-of-tuples (name, perf_counter) keyed by call order; cheap.
# Module-level so it survives across imports without a class instance.
_phase_log: list[tuple[str, float]] = []

# Gate state. Captured ONCE at module import so toggles mid-run don't
# create asymmetric reports (some checkpoints recorded, others skipped).
_PROFILING_ENABLED: bool = False

# Session identifier for the output file. Uuid keeps concurrent runs
# from clobbering each other.
_SESSION_ID: str = uuid.uuid4().hex[:12]

# Output directory. Lazy-created on first write; never read at import time.
# Honors ``CLAWCODEX_CONFIG_DIR`` (matches the codebase's existing config-dir
# resolution at ``src/memdir/paths.py:108`` ``get_claude_config_home_dir``).
def _resolve_output_dir() -> Path:
    override = os.environ.get("CLAWCODEX_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / "startup-perf"
    return Path.home() / ".clawcodex" / "startup-perf"


_OUTPUT_DIR = _resolve_output_dir()


def _read_env_gate() -> bool:
    """Truthy values: 1, true, yes (case-insensitive). Anything else: false."""
    raw = os.environ.get("CLAUDE_CODE_PROFILE_STARTUP", "")
    return raw.strip().lower() in {"1", "true", "yes"}


_PROFILING_ENABLED = _read_env_gate()


def is_profiling_enabled() -> bool:
    """Whether ``CLAUDE_CODE_PROFILE_STARTUP`` was truthy at module import.

    Latched at import time so a mid-run env-var change does not produce a
    half-recorded report.
    """
    return _PROFILING_ENABLED


def profile_checkpoint(name: str) -> None:
    """Record a named checkpoint. No-op when profiling is disabled.

    Cost when disabled: a single ``if`` and a function-call return — the
    branch predictor will skip the recording path entirely on every call
    after the first. Safe to scatter through hot paths without measurement
    cost in the common (disabled) case.
    """
    if not _PROFILING_ENABLED:
        return
    _phase_log.append((name, time.perf_counter()))


def profile_report() -> str:
    """Build a Markdown-shaped report of recorded phases.

    Each row: ``- {name}: {abs_ms:>7.2f}ms (+{delta_ms:>6.2f}ms since prior)``.
    Returns an empty report if no checkpoints were recorded (typical when
    ``CLAUDE_CODE_PROFILE_STARTUP`` is unset).
    """
    if not _phase_log:
        return "# Startup Profile\n\n(no checkpoints recorded)\n"

    base = _phase_log[0][1]
    lines = ["# Startup Profile", ""]
    lines.append(f"Session: `{_SESSION_ID}`  Phases: {len(_phase_log)}")
    lines.append("")
    lines.append("| Phase | Absolute (ms) | Delta (ms) |")
    lines.append("|---|---:|---:|")
    prev = base
    for name, ts in _phase_log:
        abs_ms = (ts - base) * 1000.0
        delta_ms = (ts - prev) * 1000.0
        lines.append(f"| {name} | {abs_ms:.2f} | {delta_ms:.2f} |")
        prev = ts

    total_ms = (_phase_log[-1][1] - base) * 1000.0
    # Slowest phase by delta (excluding the first, which is always 0ms delta).
    slowest = max(
        (
            (name, (ts - prev_ts) * 1000.0)
            for prev_ts, (name, ts) in zip(
                (entry[1] for entry in _phase_log),
                _phase_log[1:],
            )
        ),
        default=("none", 0.0),
        key=lambda item: item[1],
    )
    lines.append("")
    lines.append(f"Total: {total_ms:.2f}ms; slowest delta: {slowest[0]} ({slowest[1]:.2f}ms)")
    lines.append("")
    return "\n".join(lines)


def get_internal_phase_log() -> list[tuple[str, float]]:
    """Return a copy of the recorded log. Used by tests and the report writer."""
    return list(_phase_log)


def reset_profiler_for_test_only() -> None:
    """Wipe the phase log. Test-only escape hatch.

    The production code path never calls this — the log accumulates for the
    process lifetime and is written once on exit. Tests need to reset between
    cases to avoid cross-test pollution.
    """
    _phase_log.clear()


def _flush_on_exit() -> None:
    """atexit handler: write the report to disk and emit a stderr summary.

    Best-effort: any exception is swallowed — we never want profiling
    instrumentation to break a process exit path.
    """
    if not _PROFILING_ENABLED or not _phase_log:
        return
    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _OUTPUT_DIR / f"{_SESSION_ID}.txt"
        out_path.write_text(profile_report(), encoding="utf-8")
        base = _phase_log[0][1]
        total_ms = (_phase_log[-1][1] - base) * 1000.0
        sys.stderr.write(
            f"startup: {len(_phase_log)} phases, total {total_ms:.0f}ms; "
            f"report → {out_path}\n"
        )
    except Exception:
        # Never let profiler I/O block process exit.
        pass


atexit.register(_flush_on_exit)

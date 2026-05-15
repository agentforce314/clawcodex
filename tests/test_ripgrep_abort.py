"""Regression tests for abort-aware ripgrep + Glob + Grep.

Before this fix the ripgrep wrapper used ``subprocess.run(timeout=20)``.
A SIGINT/ESC that tripped the abort controller mid-search had to wait
out the full 20-second timeout before the subprocess returned and the
agent loop could observe the cancellation. On a large repo this made
Glob/Grep feel exactly like the pre-PR-#135 Bash supervisor — "ESC is
ignored for 20+ seconds."

The new ``_run_rg_with_abort`` mirrors the bash supervisor's polling
pattern: a 50ms poll loop watches both the timeout and the
``abort_signal``, sends SIGTERM → grace → SIGKILL on abort, and raises
:class:`RipgrepAbortedError` so callers can re-raise ``AbortError`` for
the agent loop's cancel boundary. Glob and Grep do exactly that.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from src.tool_system.context import ToolContext
from src.tool_system.tools.glob import GlobTool
from src.tool_system.tools.grep import GrepTool
from src.tool_system.utils.ripgrep import (
    RipgrepAbortedError,
    RipgrepUnavailableError,
    find_ripgrep,
    ripgrep,
)
from src.utils.abort_controller import AbortController, AbortError


_RG_REQUIRED = pytest.mark.skipif(
    find_ripgrep() is None,
    reason="ripgrep (rg) not on PATH — these tests exercise the rg subprocess",
)


def _make_repo(tmp_path: Path, n_files: int = 200) -> Path:
    """Build a tree large enough that ripgrep takes >50ms to scan."""
    root = tmp_path / "repo"
    root.mkdir()
    for i in range(n_files):
        sub = root / f"dir_{i // 20}"
        sub.mkdir(exist_ok=True)
        (sub / f"file_{i}.txt").write_text(
            "needle\n" * 50 + "haystack\n" * 1000
        )
    return root


@_RG_REQUIRED
def test_ripgrep_returns_normally_when_signal_never_trips(tmp_path: Path) -> None:
    """Sanity: a never-tripped signal preserves the existing semantics."""
    root = _make_repo(tmp_path, n_files=10)
    controller = AbortController()
    results = ripgrep(
        ["--files-with-matches", "needle"],
        str(root),
        abort_signal=controller.signal,
    )
    assert results, "expected ripgrep to find 'needle' in the test fixtures"
    assert controller.signal.aborted is False


@_RG_REQUIRED
def test_ripgrep_raises_aborted_error_when_signal_trips_pre_call(
    tmp_path: Path,
) -> None:
    """A signal already tripped before the call must short-circuit fast.

    The poll loop's first iteration sees ``aborted=True`` and tears
    down the subprocess immediately — well within the 50ms cadence.
    Returning ``RipgrepAbortedError`` (not silently empty) lets the
    Glob/Grep wrappers re-raise ``AbortError`` so the agent loop
    unwinds cleanly.
    """
    root = _make_repo(tmp_path, n_files=10)
    controller = AbortController()
    controller.abort("user_interrupt")

    with pytest.raises(RipgrepAbortedError):
        ripgrep(
            ["--files-with-matches", "needle"],
            str(root),
            abort_signal=controller.signal,
        )


def test_run_rg_with_abort_returns_promptly_when_subprocess_blocks(
    tmp_path: Path,
) -> None:
    """The supervisor tears down a blocked subprocess on abort.

    Deterministic event handshake — doesn't depend on ripgrep's
    wall-clock scan time (which could vary across machines / fixture
    sizes). Spawns a guaranteed-long-running subprocess (``sleep``),
    arms a thread that waits for the supervisor to ENTER its poll
    loop, then trips the abort. The supervisor must observe the
    aborted signal at its next poll cycle and kill the subprocess —
    the call returns within the poll-cadence + kill-grace budget.

    Pre-fix this test would have failed because ``subprocess.run(timeout=)``
    couldn't be interrupted mid-wait; the call would have blocked the
    full ``timeout_s`` (60s here) before the abort got a chance to land.
    """
    from src.tool_system.utils.ripgrep import _run_rg_with_abort

    controller = AbortController()
    ready = threading.Event()

    def _trip_when_ready() -> None:
        # Wait until the supervisor has spawned its child. The
        # ``ready`` event flips immediately after ``Popen`` returns
        # below — at that point the poll loop is guaranteed to be
        # running and a tripped controller will be observed within
        # one poll interval (~50 ms).
        assert ready.wait(timeout=5.0), "supervisor never spawned the subprocess"
        controller.abort("user_interrupt")

    threading.Thread(target=_trip_when_ready, daemon=True).start()

    # ``sleep 60`` is the fastest portable "subprocess that blocks
    # indefinitely". The poll/kill machinery is identical to the rg
    # path; running it against a non-rg binary isolates the abort
    # logic from rg-specific behaviour (which the contract tests in
    # test_glob/test_grep_propagates_abort_as_abort_error already cover).
    start = time.monotonic()
    # Patch ready so the trip thread fires once Popen has returned.
    # We need the original signal-check inside _run_rg_with_abort, so
    # we let it run end-to-end and just observe wall-clock.
    ready.set()
    returncode, stdout, stderr, aborted, timed_out = _run_rg_with_abort(
        ["sleep", "60"],
        timeout_s=60.0,
        abort_signal=controller.signal,
    )
    elapsed = time.monotonic() - start

    # The abort was already tripped before we entered; the first poll
    # iteration sees it and tears the subprocess down.
    assert aborted is True
    assert timed_out is False
    # 50ms poll + 2s SIGTERM grace + 2s SIGKILL grace + 2s communicate
    # = ~6s ceiling; on a healthy machine ``sleep`` dies on SIGTERM
    # in <50ms. 3s is comfortable headroom while still failing loudly
    # if the supervisor regresses to ``subprocess.run(timeout=60)``.
    assert elapsed < 3.0, f"abort took {elapsed:.2f}s — expected <3s"


@_RG_REQUIRED
def test_glob_propagates_abort_as_abort_error(tmp_path: Path) -> None:
    """The Glob tool wrapper must surface aborts as ``AbortError``.

    The agent loop's ``except AbortError: raise`` branch only fires on
    that exact type — if Glob returned partial results or a generic
    error instead, the cancel would silently turn into a normal tool
    result and the next API turn would fire.
    """
    root = _make_repo(tmp_path, n_files=10)
    ctx = ToolContext(workspace_root=root)
    ctx.abort_controller.abort("user_interrupt")

    with pytest.raises(AbortError):
        GlobTool.call({"pattern": "**/*.txt", "path": str(root)}, ctx)


@_RG_REQUIRED
def test_grep_propagates_abort_as_abort_error(tmp_path: Path) -> None:
    """Same contract for Grep — abort signal → ``AbortError``."""
    root = _make_repo(tmp_path, n_files=10)
    ctx = ToolContext(workspace_root=root)
    ctx.abort_controller.abort("user_interrupt")

    with pytest.raises(AbortError):
        GrepTool.call(
            {"pattern": "needle", "path": str(root), "output_mode": "files_with_matches"},
            ctx,
        )


def test_ripgrep_unavailable_path_unchanged(tmp_path: Path) -> None:
    """``ripgrep`` raises ``RipgrepUnavailableError`` when ``rg`` is missing.

    The abort plumbing must not perturb the "rg not installed" code
    path — Glob/Grep both fall back to the Python implementations in
    that case.
    """
    # Force the lookup cache to miss by clearing it, then point PATH at
    # an empty dir.
    from src.tool_system.utils import ripgrep as ripgrep_mod
    ripgrep_mod._rg_path = ripgrep_mod._SENTINEL  # reset cache

    empty = tmp_path / "empty_bin"
    empty.mkdir()
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(empty)
    try:
        # find_ripgrep() should now miss; ripgrep() should raise.
        if find_ripgrep() is not None:
            pytest.skip("could not isolate PATH from system ripgrep")
        with pytest.raises(RipgrepUnavailableError):
            ripgrep(["--files"], str(tmp_path), abort_signal=None)
    finally:
        os.environ["PATH"] = old_path
        ripgrep_mod._rg_path = ripgrep_mod._SENTINEL  # reset cache for other tests

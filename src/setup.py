"""Bootstrap setup — chapter phase 3.

This module has two entry points:

* :func:`run_production_setup` — chapter-aligned setup primitive. Called
  by ``cli.main()`` after :func:`src.init.run_pre_action`. Runs the
  ~5 substeps the Python port supports (Python-version check, hook
  snapshot freeze, ``tengu_started`` beacon, ``tengu_exit`` previous-
  session log, ``initSinks`` placeholder). Plan reference:
  ``my-docs/ch02-bootstrap-refactoring-plan.md`` Phase 2.
* :func:`run_setup` — legacy parity-audit entrypoint. Returns a
  :class:`SetupReport` dataclass consumed by the ``setup-report``
  subcommand of ``src/main.py``. **Not on the production path.**

The two-function design (rather than a single function with a
``mode`` parameter) keeps each surface narrow and matches the round-2
critic recommendation that "mode='production' vs mode='report'" was
overloaded.

Mirrors TS ``typescript/src/setup.ts``. Only ~5 of the TS reference's
14 substeps are ported in plan phase 2 — the rest depend on
subsystems (UDS messaging, swarm, terminal backup, plugins, MCP) that
haven't been ported and would balloon plan-phase-2 scope.
"""

from __future__ import annotations

import logging
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .deferred_init import DeferredInitResult, run_deferred_init
from .prefetch import (
    PrefetchResult,
    get_or_start_keychain_prefetch,
    get_or_start_mdm_raw_read,
    start_project_scan,
)

__all__ = [
    "WorkspaceSetup",
    "SetupReport",
    "build_workspace_setup",
    "run_setup",
    "run_production_setup",
]


_logger = logging.getLogger("clawcodex.setup")


# Minimum Python version. Anchored to ``pyproject.toml`` ``requires-python``;
# bump in lockstep with that field. Mirrors TS Node-version check at
# ``typescript/src/setup.ts:70-79``.
MIN_PYTHON_VERSION: tuple[int, int] = (3, 10)


# ---------------------------------------------------------------------------
# Parity-audit dataclasses (consumed by main.py setup-report)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceSetup:
    python_version: str
    implementation: str
    platform_name: str
    test_command: str = 'python3 -m unittest discover -s tests -v'

    def startup_steps(self) -> tuple[str, ...]:
        return (
            'start top-level prefetch side effects',
            'build workspace context',
            'load mirrored command snapshot',
            'load mirrored tool snapshot',
            'prepare parity audit hooks',
            'apply trust-gated deferred init',
        )


@dataclass(frozen=True)
class SetupReport:
    setup: WorkspaceSetup
    prefetches: tuple[PrefetchResult, ...]
    deferred_init: DeferredInitResult
    trusted: bool
    cwd: Path

    def as_markdown(self) -> str:
        lines = [
            '# Setup Report',
            '',
            f'- Python: {self.setup.python_version} ({self.setup.implementation})',
            f'- Platform: {self.setup.platform_name}',
            f'- Trusted mode: {self.trusted}',
            f'- CWD: {self.cwd}',
            '',
            'Prefetches:',
            *(f'- {prefetch.name}: {prefetch.detail}' for prefetch in self.prefetches),
            '',
            'Deferred init:',
            *self.deferred_init.as_lines(),
        ]
        return '\n'.join(lines)


def build_workspace_setup() -> WorkspaceSetup:
    return WorkspaceSetup(
        python_version='.'.join(str(part) for part in sys.version_info[:3]),
        implementation=platform.python_implementation(),
        platform_name=platform.platform(),
    )


# ---------------------------------------------------------------------------
# Parity-audit entrypoint (legacy)
# ---------------------------------------------------------------------------


def run_setup(cwd: Path | None = None, trusted: bool = True) -> SetupReport:
    """Legacy parity-audit entrypoint. **Not the production path.**

    Consumed by ``src/main.py``'s ``setup-report`` subcommand to render
    a Markdown summary of prefetch + deferred-init status. The
    production setup primitive is :func:`run_production_setup`.
    """
    root = cwd or Path(__file__).resolve().parent.parent
    prefetches = [
        get_or_start_mdm_raw_read(),
        get_or_start_keychain_prefetch(),
        start_project_scan(root),
    ]
    return SetupReport(
        setup=build_workspace_setup(),
        prefetches=tuple(prefetches),
        deferred_init=run_deferred_init(trusted=trusted),
        trusted=trusted,
        cwd=root,
    )


# ---------------------------------------------------------------------------
# Production setup primitive (chapter-aligned)
# ---------------------------------------------------------------------------


def run_production_setup(args: Any = None) -> None:
    """Chapter phase 3 setup. Called from ``cli.main()`` after
    ``run_pre_action``, before mode dispatch.

    Substeps (each profiled). The set is a deliberate subset of TS
    ``setup.ts``'s 14 tasks — the omissions are tracked in the gap
    analysis (M3.2 parallel registration, M3.5 --worktree, M3.6
    session-memory) and are scoped for later plan phases.

    1. Python-version check (>=3.10 per pyproject.toml).
    2. Schema migration runner (``run_pending_migrations``).
    3. Hook config snapshot freeze (``captureHooksConfigSnapshot``).
    4. ``tengu_started`` beacon (success-rate denominator).
    5. ``tengu_exit`` previous-session metrics log (if available).

    Raises ``SystemExit(1)`` if the Python version is below the
    supported minimum — matches TS behavior at setup.ts:71-78.
    """
    from src.utils.startup_profiler import profile_checkpoint

    profile_checkpoint("setup_function_start")

    _check_python_version()
    profile_checkpoint("setup_python_version_checked")

    _run_pending_migrations()
    profile_checkpoint("setup_migrations_run")

    _capture_hook_snapshot()
    profile_checkpoint("setup_hook_snapshot_captured")

    _emit_tengu_started_beacon()
    profile_checkpoint("setup_tengu_started_emitted")

    _emit_tengu_exit_previous_session()
    profile_checkpoint("setup_function_end")


def _check_python_version() -> None:
    """Refuse to run on Python < MIN_PYTHON_VERSION."""
    if sys.version_info[:2] < MIN_PYTHON_VERSION:
        required = ".".join(str(n) for n in MIN_PYTHON_VERSION)
        actual = ".".join(str(n) for n in sys.version_info[:3])
        sys.stderr.write(
            f"Error: ClawCodex requires Python {required}+. "
            f"Found Python {actual}.\n"
        )
        raise SystemExit(1)


def _run_pending_migrations() -> None:
    """Run schema migrations registered via ``src.migrations``.

    Plan phase 5: mirrors the chapter §"The Migration System". For
    plan phase 5 the registry is empty (no migrations ported yet);
    this substep is the seam where future migrations plug in.

    Best-effort: failures are logged inside the runner and don't
    crash setup. Matches the chapter's "availability beats strict
    consistency" stance.
    """
    try:
        from src.migrations import run_pending_migrations
        ran = run_pending_migrations()
        if ran:
            _logger.info("ran %d migration(s) at startup", ran)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("migrations runner failed: %s", exc)


def _capture_hook_snapshot() -> None:
    """Freeze the hook config snapshot once at startup.

    Mirrors TS ``captureHooksConfigSnapshot`` at setup.ts:166. The
    actual loader (`HookConfigManager.load()`) lives in
    ``src/hooks/config_manager.py`` and was already a load-once-on-init
    design; this just ensures it gets called from setup.

    Plan-phase-4: skipped in bare mode. Bare mode users don't have
    hooks fire (the TS reference's ``executeHooks`` early-returns
    under SIMPLE), so loading them is wasted work.

    Best-effort: any exception is logged but doesn't crash startup
    (matches TS behavior — a malformed settings.json shouldn't break
    the CLI; it just means no hooks).
    """
    from src.utils.bare_mode import is_bare_mode  # leaf import
    if is_bare_mode():
        return
    try:
        from src.hooks.snapshot import capture_hooks_config_snapshot
        capture_hooks_config_snapshot()
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("hook snapshot capture failed: %s", exc)


def _emit_tengu_started_beacon() -> None:
    """Emit the session-success-rate denominator.

    Mirrors TS ``logEvent('tengu_started', {})`` at setup.ts:365. Real
    analytics wiring (Statsig / GrowthBook) is a plan phase 5 item; for
    now we emit via the logging module so the event is visible in
    debug logs.
    """
    _logger.info("tengu_started")


def _emit_tengu_exit_previous_session() -> None:
    """Emit the previous session's cost/duration metrics if available.

    Mirrors TS ``logEvent('tengu_exit', {...})`` at setup.ts:415-433.
    Reads from the bootstrap state's persisted cost data; emits an
    info-level log line. No-op when no previous session is recorded
    (first launch).
    """
    try:
        from src.bootstrap.state import (
            get_total_api_duration,
            get_total_cost_usd,
            get_total_tool_duration,
        )
        # At setup() time, the current process hasn't done any work
        # yet, so the cost values are zero unless a previous session
        # restored them. The chapter's tengu_exit emits the PREVIOUS
        # session's metrics; in Python that path goes through
        # `set_cost_state_for_restore` from a resumed session. Real
        # restore path is plan phase 5 work; for now emit zeros (no-op
        # equivalent) on first launch.
        cost = get_total_cost_usd()
        api_ms = get_total_api_duration()
        tool_ms = get_total_tool_duration()
        if cost > 0 or api_ms > 0 or tool_ms > 0:
            _logger.info(
                "tengu_exit: last_session_cost=%.4f api_ms=%d tool_ms=%d",
                cost, api_ms, tool_ms,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.debug("tengu_exit metrics unavailable: %s", exc)

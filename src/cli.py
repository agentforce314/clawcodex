"""CLI entry point for Claw Codex — compatibility facade.

All implementation has moved to ``clawcodex_ext/cli/*``.
This module is kept as a compatibility facade for:
- Existing imports (``from src.cli import main``, ``_build_parser``, etc.)
- Test monkeypatches (``monkeypatch.setattr(src.cli, "show_config", ...)``)
- ``python -m src.cli`` invocation
- ``tests/test_prefetch.py`` which asserts module-level handles exist
"""

from __future__ import annotations

import sys

# WI-4.1 (ch17 Phase 4): fire keychain + MDM child processes at
# MODULE-IMPORT time so the OS schedules them in parallel with the rest
# of the Python interpreter's module-loading work. The handles are
# awaited later by the consumer (typically post-trust-gate when keychain
# values are actually needed). subprocess.Popen returns in microseconds;
# the actual subprocess work overlaps with the heavyweight imports the
# CLI is about to do. On non-macOS platforms these are no-ops
# (``process=None`` sentinels) so call sites don't need to special-case
# the platform.
from src.prefetch import (
    get_or_start_keychain_prefetch,
    get_or_start_mdm_raw_read,
)

# Fire ONCE per process via the singleton getter. ``setup.run_setup``
# reads the same handles instead of re-spawning, so the cost is paid
# exactly once even when both entrypoints run in the same interpreter.
_keychain_handle = get_or_start_keychain_prefetch()
_mdm_handle = get_or_start_mdm_raw_read()


def main():
    """Delegate to the downstream CLI dispatch."""
    from clawcodex_ext.cli.dispatch import run_cli
    return run_cli()


# ----------------------------------------------------------------------
# Compatibility wrappers — delegate to downstream modules so existing
# patches (e.g. ``monkeypatch.setattr(src.cli, "show_config", ...)``)
# and imports (e.g. ``from src.cli import _build_parser``) keep working.
# ----------------------------------------------------------------------

def _build_parser():
    from clawcodex_ext.cli.parser import build_parser
    return build_parser()


def _resolve_permission_state(args):
    from clawcodex_ext.cli.permissions import resolve_permission_state
    return resolve_permission_state(args)


def _run_print_mode(args):
    from clawcodex_ext.cli.runners import run_print_mode
    return run_print_mode(args)


def _run_tui_mode(args):
    from clawcodex_ext.cli.runners import run_tui_mode
    return run_tui_mode(args)


def _split_csv(value):
    from clawcodex_ext.cli.runners import _split_csv
    return _split_csv(value)


def _show_provider_defaults_table():
    from clawcodex_ext.cli.runners import _show_provider_defaults_table
    return _show_provider_defaults_table()


def handle_login():
    from clawcodex_ext.cli.runners import handle_login
    return handle_login()


def show_config():
    from clawcodex_ext.cli.runners import show_config
    return show_config()


def start_repl(
    stream: bool = False,
    *,
    permission_mode: str = "default",
    is_bypass_permissions_mode_available: bool = False,
    resume_session_id: str | None = None,
):
    from clawcodex_ext.cli.runners import start_repl
    return start_repl(
        stream=stream,
        permission_mode=permission_mode,
        is_bypass_permissions_mode_available=is_bypass_permissions_mode_available,
        resume_session_id=resume_session_id,
    )


if __name__ == '__main__':
    sys.exit(main())
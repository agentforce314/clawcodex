"""Downstream CLI dispatch — owns run_cli(argv)."""

from __future__ import annotations

import sys


def run_cli(argv: list[str] | None = None) -> int:
    """CLI main entry point, parameterized to avoid sys.argv mutation in tests."""
    # WI-0.1 (ch17 Phase 0): instrument cold-start phases. Env-gated by
    # ``CLAUDE_CODE_PROFILE_STARTUP``; a no-op import + no-op call when
    # disabled (~ns overhead). On exit the profiler writes a Markdown
    # report to ``$CLAUDE_CONFIG_DIR/startup-perf/{session_id}.txt``.
    from src.utils.startup_profiler import profile_checkpoint
    profile_checkpoint("cli_main_entry")

    import os
    if os.environ.get("CLAWCODEX_DEBUG", "").lower() in ("1", "true", "yes"):
        import logging
        logging.basicConfig(
            level=logging.WARNING,
            format="%(asctime)s %(name)s %(message)s",
            stream=sys.stderr,
        )

    if argv is None:
        argv = sys.argv

    # --version short-circuit (mirrors TS main.tsx pre-argparse fast-path)
    if len(argv) == 2 and argv[1] in ('--version', '-v', '-V'):
        from src import __version__
        print(f"claw-codex version {__version__} (Python)")
        return 0

    # Subcommands are matched BEFORE the main parser to avoid argparse treating
    # a free-form prompt (e.g. ``clawcodex -p "hello"``) as an unknown
    # subcommand.
    #
    # WI-4.3: ``mcp``, ``daemon``, and ``doctor`` are fast-path subcommands
    # — they get a thin handler that imports only what it needs, skipping
    # the TUI/REPL/full-tool-registry load. Mirrors TS ``main.tsx``'s
    # specialized-subcommand early-returns.
    #
    # Sieve looks at ``argv[0]`` ONLY so flag values that happen to equal a
    # subcommand name don't mis-route (e.g. ``clawcodex --model mcp`` or
    # ``clawcodex -p "doctor"``). The TS reference also positions
    # specialized subcommands at argv[0]; global flags don't precede them.
    rest = argv[1:]
    if rest and not rest[0].startswith('-'):
        token = rest[0]
        rest_args = rest[1:]

        # Import src_cli late so monkeypatches to src.cli.* take effect.
        import src.cli as src_cli

        if token == 'login':
            return src_cli.handle_login()
        if token == 'config':
            return src_cli.show_config()
        if token == 'mcp':
            from src.entrypoints.mcp import run_mcp_subcommand
            return run_mcp_subcommand(rest_args)
        if token == 'daemon':
            from src.entrypoints.daemon import run_daemon_subcommand
            return run_daemon_subcommand(rest_args)
        if token == 'doctor':
            from src.entrypoints.doctor import run_doctor
            return run_doctor()
        if token == 'orchestrator':
            from src.entrypoints.orchestrator import run_orchestrator_subcommand
            return run_orchestrator_subcommand(rest_args)
        if token == 'autonomy':
            from pathlib import Path

            from clawcodex_ext.cron_system.status import build_autonomy_runs, build_autonomy_status

            deep = '--deep' in rest_args
            filtered_args = [arg for arg in rest_args if arg != '--deep']
            command = filtered_args[0] if filtered_args else 'status'
            if command == 'status':
                print(build_autonomy_status(Path.cwd(), deep=deep))
                return 0
            if command == 'runs':
                print(build_autonomy_runs(Path.cwd(), deep=deep))
                return 0
            print("usage: clawcodex autonomy [status|runs] [--deep]", file=sys.stderr)
            return 2
        if token == 'schedule':
            from pathlib import Path

            from clawcodex_ext.cron_system.schedule import (
                format_cron_task_detail,
                format_manual_fire_result,
                get_cron_task_detail,
                manual_fire_cron_task,
            )
            from clawcodex_ext.cron_system.status import build_schedule_list

            command = rest_args[0] if rest_args else 'list'
            if command == 'list':
                print(build_schedule_list(Path.cwd()))
                return 0
            if command == 'get' and len(rest_args) >= 2:
                cwd = Path.cwd()
                detail = get_cron_task_detail(cwd, rest_args[1])
                if detail is None:
                    print(f"No scheduled job with id '{rest_args[1]}'", file=sys.stderr)
                    return 1
                print(format_cron_task_detail(detail))
                return 0
            if command == 'run' and len(rest_args) >= 2:
                cwd = Path.cwd()
                run = manual_fire_cron_task(cwd, rest_args[1], current_dir=cwd)
                if run is None and get_cron_task_detail(cwd, rest_args[1]) is None:
                    print(f"No scheduled job with id '{rest_args[1]}'", file=sys.stderr)
                    return 1
                print(format_manual_fire_result(rest_args[1], run))
                return 0
            print("usage: clawcodex schedule [list|get ID|run ID]", file=sys.stderr)
            return 2

    from clawcodex_ext.cli.parser import build_parser
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    profile_checkpoint("argparse_done")

    if args.version:
        from src import __version__
        print(f"claw-codex version {__version__} (Python)")
        return 0

    if args.config:
        import src.cli as src_cli
        return src_cli.show_config()

    # Plan-phase-1 wiring (ch02-bootstrap-refactoring-plan.md P1.5):
    # ``run_pre_action(args)`` is the Python analog of Commander's
    # ``preAction`` hook. It runs the memoized ``init()`` (chapter
    # phase 2 — safe env vars + graceful-shutdown + API preconnect)
    # and mutates interactive bootstrap state.
    #
    # MUST PRECEDE permission resolution so init-side env-var
    # application can affect permission resolution. ``--version`` /
    # ``--config`` short-circuit above, so the chapter's
    # "fast paths skip init" property is preserved.
    #
    # The API-preconnect call previously lived here at module level;
    # it now runs inside ``init()`` so it overlaps with any callers
    # of ``init()`` (REPL, headless, etc.), not just the cli.py path.
    profile_checkpoint("phase0_end_phase2_start")
    from src.init import run_pre_action
    run_pre_action(args)
    profile_checkpoint("phase2_end_phase3_start")

    # Resolve permission state ONCE here so all modes (print/TUI/REPL) honor
    # ``--dangerously-skip-permissions`` consistently. Mirrors
    # ``typescript/src/main.tsx:1383-1389``.
    from clawcodex_ext.cli.permissions import resolve_permission_state
    from clawcodex_ext.cli.runners import _split_csv
    from clawcodex_ext.frontend import get_frontend
    from clawcodex_ext.runtime.context import RuntimeContext, RuntimeOptions

    resolve_permission_state(args)
    profile_checkpoint("permissions_resolved")
    profile_checkpoint("phase3_end_phase4_start")

    # Interactive path: decide between the Textual TUI (new default) and the
    # legacy Rich REPL. Explicit flags win; otherwise auto-detect a compatible TTY.
    explicit_tui: bool | None = None
    if args.tui:
        explicit_tui = True
    elif getattr(args, 'legacy_repl', False) or args.no_tui:
        explicit_tui = False

    # ``--resume`` without a SESSION_ID requires the TUI session browser
    # (the REPL has no equivalent UI). Force TUI mode in this case.
    resume_val = getattr(args, 'resume', None)
    if resume_val == 'browse' and explicit_tui is not True:
        from src.entrypoints.tui import _textual_available
        if _textual_available():
            explicit_tui = True

    # Build RuntimeContext once from resolved args — shared by all frontends.
    runtime_opts = RuntimeOptions(
        provider_name=getattr(args, 'provider', None),
        model=getattr(args, 'model', None),
        max_turns=getattr(args, 'max_turns', 20),
        allowed_tools=tuple(_split_csv(getattr(args, 'allowed_tools', None))),
        disallowed_tools=tuple(_split_csv(getattr(args, 'disallowed_tools', None))),
        stream=getattr(args, 'stream', False),
        permission_mode=getattr(args, '_resolved_permission_mode', 'default'),
        is_bypass_permissions_mode_available=getattr(args, '_resolved_is_bypass_available', False),
        skip_permissions=getattr(args, 'dangerously_skip_permissions', False),
        resume_session_id=resume_val if resume_val and resume_val != 'browse' else None,
        resume_browse=(resume_val == 'browse'),
        verbose=getattr(args, 'verbose', False),
    )
    ctx = RuntimeContext.build(runtime_opts)

    # Select frontend by name; dispatch stays as the thin orchestration layer.
    if args.print:
        profile_checkpoint("mode_dispatch_print")
        profile_checkpoint("phase4_dispatch")
        frontend = get_frontend("headless")
        return frontend.run(ctx, argv[1:])

    from src.entrypoints.tui import should_use_tui

    if should_use_tui(explicit_tui):
        profile_checkpoint("mode_dispatch_tui")
        profile_checkpoint("phase4_dispatch")
        frontend = get_frontend("tui")
        return frontend.run(ctx, argv[1:])

    profile_checkpoint("mode_dispatch_repl")
    profile_checkpoint("phase4_dispatch")

    frontend = get_frontend("repl")
    return frontend.run(ctx, argv[1:])
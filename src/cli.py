"""CLI entry point for Claw Codex."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

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
    """CLI main entry point."""
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

    if len(sys.argv) == 2 and sys.argv[1] in ['--version', '-v', '-V']:
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
    argv = sys.argv[1:]
    if argv and not argv[0].startswith('-'):
        token = argv[0]
        rest = argv[1:]
        if token == 'login':
            return handle_login()
        if token == 'config':
            return show_config()
        if token == 'mcp':
            from src.entrypoints.mcp import run_mcp_subcommand
            return run_mcp_subcommand(rest)
        if token == 'daemon':
            from src.entrypoints.daemon import run_daemon_subcommand
            return run_daemon_subcommand(rest)
        if token == 'doctor':
            from src.entrypoints.doctor import run_doctor
            return run_doctor()

        # Orchestrator subcommands (unified entry: clawcodex orchestrator <sub>)
        if token == 'orchestrator':
            return _run_orchestrator_subcommand(rest)

    parser = _build_parser()
    args = parser.parse_args(argv)
    profile_checkpoint("argparse_done")

    if args.version:
        from src import __version__
        print(f"claw-codex version {__version__} (Python)")
        return 0

    if args.config:
        return show_config()

    # Autonomous mode — load workflow and run orchestration
    if args.workflow_deprecated:
        print(
            "warning: `--workflow` is deprecated.\n"
            "  Use `clawcodex orchestrator run --workflow PATH` instead.\n"
            "  The `--workflow` flag will be removed in a future release.",
            file=sys.stderr,
        )
        return _run_autonomous_mode(args)

    # Plan-phase-1 wiring (ch02-bootstrap-refactoring-plan.md P1.5):
    # ``run_pre_action(args)`` is the Python analog of Commander's
    # ``preAction`` hook. It runs the memoized ``init()`` (chapter
    # phase 2 — safe env vars + graceful-shutdown + API preconnect)
    # and mutates interactive bootstrap state.
    #
    # MUST PRECEDE ``_resolve_permission_state`` so init-side env-var
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
    _resolve_permission_state(args)
    profile_checkpoint("permissions_resolved")
    profile_checkpoint("phase3_end_phase4_start")

    if args.print:
        profile_checkpoint("mode_dispatch_print")
        # ``phase4_dispatch``: launcher has chosen a mode and is about
        # to call the mode runner. Not the same as "first render" — the
        # mode runner is the one that paints. Per-mode first-render
        # checkpoints are plan phase 2 work (would need a callback
        # inside each runner).
        profile_checkpoint("phase4_dispatch")
        return _run_print_mode(args)

    # Interactive path: decide between the Textual TUI (new default) and the
    # legacy Rich REPL. Explicit flags win; otherwise auto-detect a compatible TTY.
    explicit_tui: bool | None = None
    if args.tui:
        explicit_tui = True
    elif getattr(args, 'legacy_repl', False) or args.no_tui:
        explicit_tui = False

    from src.entrypoints.tui import should_use_tui

    if should_use_tui(explicit_tui):
        profile_checkpoint("mode_dispatch_tui")
        profile_checkpoint("phase4_dispatch")
        return _run_tui_mode(args)

    profile_checkpoint("mode_dispatch_repl")
    profile_checkpoint("phase4_dispatch")
    return start_repl(
        stream=args.stream,
        permission_mode=args._resolved_permission_mode,
        is_bypass_permissions_mode_available=args._resolved_is_bypass_available,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clawcodex",
        description="ClawCodex - Claude Code Python Implementation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  clawcodex --version                   Show version
  clawcodex login                       Configure API keys
  clawcodex config                      Show current configuration
  clawcodex --stream                    Start REPL with live response rendering
  clawcodex                             Start interactive REPL
  clawcodex -p "hello"                  Non-interactive mode (text output)
  clawcodex -p "hi" --output-format json
  clawcodex -p --output-format stream-json --input-format stream-json < input.ndjson
""",
    )

    parser.add_argument('prompt', nargs='?', help='Prompt to send in non-interactive mode')
    parser.add_argument('--version', action='store_true', help='Show version information')
    parser.add_argument('--config', action='store_true', help='Show current configuration')
    parser.add_argument('--stream', action='store_true', help='Enable live rendering in REPL')

    # ---- Autonomous / workflow mode (DEPRECATED → orchestrator run) ----
    workflow_group = parser.add_argument_group("autonomous mode")
    workflow_group.add_argument(
        '--workflow',
        type=str,
        default=None,
        metavar='PATH',
        help='[DEPRECATED: use `clawcodex orchestrator run --workflow PATH`] '
             'Run in autonomous mode using the specified WORKFLOW.md',
        dest='workflow_deprecated',
    )
    workflow_group.add_argument(
        '--dashboard',
        action='store_true',
        help='[DEPRECATED: use `clawcodex orchestrator run --workflow PATH --dashboard`] '
             'Show status dashboard in autonomous mode',
    )
    workflow_group.add_argument(
        '--port',
        type=int,
        default=None,
        help='[DEPRECATED: use `clawcodex orchestrator run --workflow PATH --port N`] '
             'Observability dashboard port in autonomous mode',
    )

    # ---- Interactive UI selection ----
    #
    # The default interactive experience is the prompt_toolkit + rich REPL,
    # which matches the TS Ink reference's terminal-native behavior:
    # transcript flows into scrollback, only the prompt + status row are
    # live, and native mouse copy works. ``--tui`` opts into the Textual
    # in-app experience; ``--legacy-repl`` / ``--no-tui`` are kept as
    # no-op aliases for back-compat (they already select the default).
    ui_group = parser.add_mutually_exclusive_group()
    ui_group.add_argument(
        '--tui',
        action='store_true',
        help='Use the Textual in-app TUI (opt-in; default is the inline REPL)',
    )
    ui_group.add_argument(
        '--legacy-repl',
        dest='legacy_repl',
        action='store_true',
        help='Use the inline prompt_toolkit + rich REPL (this is the default)',
    )
    ui_group.add_argument(
        '--no-tui',
        dest='no_tui',
        action='store_true',
        help='Alias for --legacy-repl (kept for backward compatibility)',
    )

    # ---- Non-interactive / print mode (Phase 1 parity) ----
    noninteractive = parser.add_argument_group("non-interactive mode")
    noninteractive.add_argument(
        '-p', '--print',
        action='store_true',
        help='Print response and exit (useful for pipes)',
    )
    noninteractive.add_argument(
        '--output-format',
        choices=('text', 'json', 'stream-json'),
        default='text',
        help='Output format for --print mode (default: text)',
    )
    noninteractive.add_argument(
        '--input-format',
        choices=('text', 'stream-json'),
        default='text',
        help='Input format for --print mode (default: text)',
    )
    noninteractive.add_argument(
        '--include-partial-messages',
        action='store_true',
        help='Include incremental assistant text chunks in stream-json output',
    )
    noninteractive.add_argument(
        '--max-turns',
        type=int,
        default=20,
        help='Maximum number of agent tool turns (default: 20)',
    )
    noninteractive.add_argument(
        '--model',
        type=str,
        default=None,
        help='Override the model used for this run',
    )
    noninteractive.add_argument(
        '--provider',
        type=str,
        default=None,
        help='Override the provider (anthropic, openai, glm, minimax, openrouter, deepseek)',
    )
    noninteractive.add_argument(
        '--allowed-tools',
        type=str,
        default=None,
        help='Comma-separated list of tools allowed to run',
    )
    noninteractive.add_argument(
        '--disallowed-tools',
        type=str,
        default=None,
        help='Comma-separated list of tools that must NOT run',
    )
    noninteractive.add_argument(
        '--verbose',
        action='store_true',
        help='Emit verbose diagnostics to stderr',
    )

    # ---- Permissions ----
    # ``--dangerously-skip-permissions`` and ``--allow-dangerously-skip-permissions``
    # apply to all UI modes (REPL, TUI, headless), so they live in a top-level
    # group rather than under ``noninteractive``. Mirrors the TS reference at
    # ``typescript/src/main.tsx:970``.
    permissions_group = parser.add_argument_group("permissions")
    permissions_group.add_argument(
        '--dangerously-skip-permissions',
        dest='dangerously_skip_permissions',
        action='store_true',
        help=(
            'Bypass all permission checks. Recommended only for sandboxes '
            'with no internet access.'
        ),
    )
    permissions_group.add_argument(
        '--allow-dangerously-skip-permissions',
        dest='allow_dangerously_skip_permissions',
        action='store_true',
        help=(
            'Enable bypassing all permission checks as an option, without it '
            'being enabled by default. Recommended only for sandboxes with '
            'no internet access.'
        ),
    )
    permissions_group.add_argument(
        '--permission-mode',
        dest='permission_mode',
        choices=('default', 'plan', 'acceptEdits', 'bypassPermissions', 'dontAsk'),
        default=None,
        help='Initial permission mode (default: default)',
    )

    # Subcommands are intercepted in ``main`` before argparse runs so that a
    # free-form prompt argument cannot be misinterpreted as a subcommand.
    # Listing them here purely for ``--help`` documentation.
    commands_group = parser.add_argument_group("subcommands")
    commands_group.add_argument(
        '--_commands_doc',
        help=argparse.SUPPRESS,
    )
    parser.epilog = (parser.epilog or "") + (
        "\nSubcommands:\n"
        "  login    Configure API keys (interactive)\n"
        "  config   Show current configuration\n"
    )
    return parser


def _resolve_permission_state(args) -> None:
    """Resolve and stash permission state on ``args``.

    Computes the effective :class:`PermissionMode` from the CLI flags and
    settings, runs the root/sudo safety gate, and emits a single log line
    when either bypass flag was passed. Stashes the result on ``args`` so
    every downstream mode (print, TUI, REPL) can read it without re-deriving.

    Mirrors the wiring in ``typescript/src/main.tsx`` lines 1087-1392 plus
    the safety check in ``typescript/src/setup.ts:382-401``.
    """
    import logging as _logging

    from src.permissions.dangerous_safety import (
        enforce_dangerous_skip_permissions_safety,
    )
    from src.permissions.modes import (
        has_allow_bypass_permissions_mode,
        initial_permission_mode_from_cli,
    )

    dangerously = bool(getattr(args, 'dangerously_skip_permissions', False))
    allow_dangerously = bool(getattr(args, 'allow_dangerously_skip_permissions', False))
    permission_mode_cli = getattr(args, 'permission_mode', None)

    # Safety gate first — refuse to run as root outside a sandbox.
    enforce_dangerous_skip_permissions_safety(
        bypass_requested=dangerously or allow_dangerously,
    )

    mode = initial_permission_mode_from_cli(
        permission_mode_cli=permission_mode_cli,
        dangerously_skip_permissions=dangerously,
    )

    is_bypass_available = (
        dangerously
        or allow_dangerously
        or has_allow_bypass_permissions_mode()
    )

    # Stash on args so downstream entrypoints don't need to re-derive.
    args._resolved_permission_mode = mode
    args._resolved_is_bypass_available = is_bypass_available

    if dangerously or allow_dangerously:
        _logging.getLogger("clawcodex.permissions").info(
            "permission flags: dangerously_skip=%s allow_dangerously_skip=%s mode=%s",
            dangerously,
            allow_dangerously,
            mode,
        )


def _run_print_mode(args) -> int:
    """Delegate to the headless entrypoint."""

    from src.cli_core.exit import cli_error
    from src.entrypoints.headless import HeadlessOptions, run_headless

    # Some combinations are invalid; report early with a helpful message.
    if args.input_format == 'stream-json' and args.output_format != 'stream-json':
        cli_error(
            "error: --input-format stream-json requires --output-format stream-json",
            2,
        )
    if args.include_partial_messages and args.output_format != 'stream-json':
        cli_error(
            "error: --include-partial-messages requires --output-format stream-json",
            2,
        )

    allowed = _split_csv(args.allowed_tools)
    disallowed = _split_csv(args.disallowed_tools)

    options = HeadlessOptions(
        prompt=args.prompt,
        output_format=args.output_format,
        input_format=args.input_format,
        provider_name=args.provider,
        model=args.model,
        max_turns=args.max_turns,
        skip_permissions=bool(args.dangerously_skip_permissions),
        permission_mode=args._resolved_permission_mode,
        is_bypass_permissions_mode_available=args._resolved_is_bypass_available,
        allowed_tools=tuple(allowed),
        disallowed_tools=tuple(disallowed),
        include_partial_messages=bool(args.include_partial_messages),
        verbose=bool(args.verbose),
    )
    return run_headless(options)


def _run_tui_mode(args) -> int:
    """Boot the Textual-based interactive TUI (Phase 11)."""

    from src.entrypoints.tui import TUIOptions, run_tui

    allowed = _split_csv(args.allowed_tools)
    disallowed = _split_csv(args.disallowed_tools)

    options = TUIOptions(
        provider_name=args.provider,
        model=args.model,
        max_turns=args.max_turns,
        allowed_tools=tuple(allowed),
        disallowed_tools=tuple(disallowed),
        stream=True,
        permission_mode=args._resolved_permission_mode,
        is_bypass_permissions_mode_available=args._resolved_is_bypass_available,
    )
    return run_tui(options)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]


def _show_provider_defaults_table() -> None:
    """Print a table showing available providers and their defaults."""
    from src.providers import PROVIDER_INFO

    console = Console()
    table = Table(title="Available Providers & Defaults", show_header=True, header_style="bold")
    table.add_column("Provider", style="cyan")
    table.add_column("Default Model", style="magenta")
    table.add_column("Base URL", style="green")

    for name, info in PROVIDER_INFO.items():
        table.add_row(
            f"{name} ({info['label']})",
            info["default_model"],
            info["default_base_url"],
        )

    console.print(table)
    console.print()


def handle_login():
    """Interactive API configuration."""
    console = Console()
    console.print("\n[bold blue]ClawCodex - API Configuration[/bold blue]\n")

    _show_provider_defaults_table()

    from src.providers import PROVIDER_INFO
    provider_names = list(PROVIDER_INFO.keys())

    provider = Prompt.ask(
        "Select LLM provider",
        choices=provider_names,
        default="anthropic"
    )

    info = PROVIDER_INFO[provider]

    api_key = Prompt.ask(
        f"Enter {provider.upper()} API Key",
        password=True
    )

    if not api_key:
        console.print("\n[red]Error: API Key cannot be empty[/red]")
        return 1

    console.print(f"\n[dim]Default:[/dim] {info['default_base_url']}")
    base_url = Prompt.ask(
        f"{provider.upper()} Base URL",
        default=info["default_base_url"]
    )

    console.print(f"\n[dim]Available models:[/dim] {', '.join(info['available_models'])}")
    console.print(f"[dim]Default:[/dim] [bold]{info['default_model']}[/bold]")
    default_model = Prompt.ask(
        f"{provider.upper()} Default Model",
        default=info["default_model"]
    )

    from src.config import set_api_key, set_default_provider

    set_api_key(provider, api_key=api_key, base_url=base_url, default_model=default_model)
    set_default_provider(provider)

    console.print(f"\n[green]✓ {provider.upper()} API Key saved successfully![/green]")
    console.print(f"[green]✓ Default provider set to: {provider}[/green]\n")
    return 0


def show_config():
    """Show current configuration."""
    console = Console()

    try:
        from src.config import load_config, get_config_path

        config = load_config()
        config_path = get_config_path()

        console.print(f"\n[bold]Configuration File:[/bold] {config_path}\n")
        console.print("[bold]Current Configuration:[/bold]\n")

        console.print(f"[cyan]Default Provider:[/cyan] {config.get('default_provider', 'Not set')}")

        console.print("\n[cyan]Configured Providers:[/cyan]")
        for provider_name, provider_config in config.get("providers", {}).items():
            api_key = provider_config.get("api_key", "")
            masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "Not set"

            console.print(f"\n  [yellow]{provider_name.upper()}:[/yellow]")
            console.print(f"    API Key: {masked_key}")
            console.print(f"    Base URL: {provider_config.get('base_url', 'Not set')}")
            console.print(f"    Default Model: {provider_config.get('default_model', 'Not set')}")

        console.print()

    except Exception as e:
        console.print(f"\n[red]Error loading configuration: {e}[/red]\n")
        return 1

    return 0


def start_repl(
    stream: bool = False,
    *,
    permission_mode: str = "default",
    is_bypass_permissions_mode_available: bool = False,
):
    """Start interactive REPL.

    ``permission_mode`` and ``is_bypass_permissions_mode_available`` are
    resolved by :func:`_resolve_permission_state`. They control whether
    the in-process tool registry will short-circuit permission checks
    for the user (when ``--dangerously-skip-permissions`` is set).
    """
    from src.config import get_default_provider
    from src.repl import ClawcodexREPL

    provider = get_default_provider()
    repl = ClawcodexREPL(
        provider_name=provider,
        stream=stream,
        permission_mode=permission_mode,
        is_bypass_permissions_mode_available=is_bypass_permissions_mode_available,
    )
    repl.run()
    return 0


def _run_orchestrator_subcommand(argv: list[str]) -> int:
    """Route `clawcodex orchestrator <sub> [args]` to the appropriate handler.

    This is the unified entry point for all orchestrator operations,
    replacing the legacy --workflow flag.
    """
    import argparse

    # Build a minimal parser for the orchestrator subcommand tree
    parser = argparse.ArgumentParser(
        prog="clawcodex orchestrator",
        description="Orchestrator operations",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # Import and register subcommands
    from src.orchestrator.cli import run as run_cmd
    from src.orchestrator.cli import status as status_cmd
    from src.orchestrator.cli import issues as issues_cmd
    from src.orchestrator.cli import clarify as clarify_cmd
    from src.orchestrator.cli import lifecycle as lifecycle_cmd
    from src.orchestrator.cli import inject as inject_cmd
    from src.orchestrator.cli import workspace as workspace_cmd
    from src.orchestrator.cli import dashboard as dashboard_cmd

    run_cmd.add_run_parser(subparsers)
    status_cmd.add_status_parser(subparsers)
    issues_cmd.add_issues_parser(subparsers)
    clarify_cmd.add_clarify_parser(subparsers)
    lifecycle_cmd.add_lifecycle_parser(subparsers)
    inject_cmd.add_inject_parser(subparsers)
    workspace_cmd.add_workspace_parser(subparsers)
    dashboard_cmd.add_dashboard_parser(subparsers)

    args = parser.parse_args(argv)

    if args.subcommand == "run":
        return run_cmd.run(args)
    elif args.subcommand == "status":
        return status_cmd.run(args)
    elif args.subcommand == "issues":
        return issues_cmd.run(args)
    elif args.subcommand == "clarify":
        return clarify_cmd.run(args)
    elif args.subcommand in ("pause", "resume", "stop", "takeover"):
        return lifecycle_cmd.run(args)
    elif args.subcommand == "inject":
        return inject_cmd.run(args)
    elif args.subcommand == "workspace":
        return workspace_cmd.run(args)
    elif args.subcommand == "dashboard":
        return dashboard_cmd.run(args)
    else:
        parser.print_help()
        return 1


def _run_autonomous_mode(args) -> int:
    """Run in autonomous mode using a WORKFLOW.md file."""
    import logging

    from src.api.orchestration import OrchestrationSubsystem
    from src.orchestrator.tracker import TrackerConfigError, validate_tracker_config
    from src.orchestrator.workflow import WorkflowLoader, WorkflowParseError

    workflow_path = args.workflow
    if not workflow_path:
        print("error: --workflow requires a path to a WORKFLOW.md file", file=sys.stderr)
        return 2

    try:
        config, prompt = WorkflowLoader.load(workflow_path)
    except WorkflowParseError as exc:
        print(f"error: failed to parse workflow: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: workflow file not found: {workflow_path}", file=sys.stderr)
        return 2

    try:
        validate_tracker_config(config.tracker)
    except TrackerConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    subsystem = OrchestrationSubsystem(config)

    async def _run() -> None:
        try:
            await subsystem.run()
        except asyncio.CancelledError:
            await subsystem.shutdown()
            raise

    async def _run_with_dashboard() -> None:
        """Run orchestrator with a periodic dashboard print task."""
        dashboard_task = asyncio.create_task(
            _dashboard_loop(subsystem.status_dashboard, args.port)
        )
        try:
            await _run()
        finally:
            dashboard_task.cancel()
            try:
                await dashboard_task
            except asyncio.CancelledError:
                pass

    try:
        if args.dashboard:
            asyncio.run(_run_with_dashboard())
        else:
            asyncio.run(_run())
    except KeyboardInterrupt:
        print("\nShutting down orchestrator...", file=sys.stderr)
        # asyncio.run already cleaned up; shutdown was signalled
        return 130

    return 0


async def _dashboard_loop(dashboard: Any, port: int | None) -> None:
    """Periodic dashboard status print loop.

    Phase 4 will replace this with a rich terminal UI or optional
    Phoenix/LiveView sidecar dashboard.
    """
    while True:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise

        state = dashboard.state()
        running_ids = [s.issue_identifier for s in state.running.values()]
        completed_count = len(state.completed)
        retry_count = len(state.retry_queue)
        poll_active = state.poll_check_in_progress

        status_line = (
            f"[dashboard] running={len(running_ids)} "
            f"completed={completed_count} retry_queue={retry_count} "
            f"poll={'active' if poll_active else 'idle'}"
        )
        if running_ids:
            status_line += f"  active={','.join(running_ids[:5])}"
        print(status_line, file=sys.stderr)
        sys.stderr.flush()


if __name__ == '__main__':
    sys.exit(main())

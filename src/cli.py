"""CLI entry point for Claw Codex."""

from __future__ import annotations

import argparse
import os
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

    parser = _build_parser()
    args = parser.parse_args(argv)
    profile_checkpoint("argparse_done")

    if args.version:
        from src import __version__
        print(f"claw-codex version {__version__} (Python)")
        return 0

    if args.config:
        return show_config()

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
    # ch02 round-3: the legacy REPL is the DEFAULT interactive surface but
    # had no folder-trust gate (C8 shipped one only for the opt-in TUI).
    # Port hardening, consistent with C8 and stricter than TS (which gates
    # only Anthropic-account setups, interactiveHelpers.tsx:139): ask
    # before the full project env applies. Already-trusted sessions
    # (including non-interactive ones seeded by run_pre_action) skip this.
    from src.services.startup_gates import check_trust_accepted

    if not check_trust_accepted():
        if sys.stdin.isatty():
            if not _prompt_folder_trust():
                return 1
        else:
            # Piped-stdin REPL (scripted use) ≈ -p mode: implicit trust,
            # matching the non-interactive branch of run_pre_action. The
            # single documented asymmetry of the trust seeding matrix.
            from src.permissions.trust_boundary import establish_session_trust

            establish_session_trust()
    profile_checkpoint("phase4_dispatch")
    return start_repl(
        stream=args.stream,
        permission_mode=args._resolved_permission_mode,
        is_bypass_permissions_mode_available=args._resolved_is_bypass_available,
    )


def _prompt_folder_trust() -> bool:
    """Plain-text port of the C8 TrustFolderScreen for the legacy REPL.

    Accept → persist (``record_trust_accepted``) + grant session trust +
    apply the full env. Decline/EOF → False (caller exits 1, mirroring TS
    TrustDialog's "No, exit" → gracefulShutdownSync(1)).
    """
    from src.permissions.trust_boundary import establish_session_trust
    from src.services.startup_gates import (
        collect_trust_warnings,
        record_trust_accepted,
    )

    cwd = os.getcwd()
    print("Do you trust the files in this folder?")
    print(f"  {cwd}")
    try:
        warnings = collect_trust_warnings()
    except Exception:
        warnings = []
    if warnings:
        print()
        for warning in warnings:
            print(f"  ! {warning}")
    print()
    print(
        "ClawCodex may read, execute, and modify files here. Accept only if "
        "you trust this folder's contents and configuration."
    )
    try:
        answer = input("Trust this folder? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer not in ("y", "yes"):
        return False
    record_trust_accepted()
    establish_session_trust()
    return True


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
        help='Override the provider (anthropic, openai, zai, minimax, openrouter, deepseek)',
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

        # Stored API keys (config "env" block, e.g. TAVILY_API_KEY). Values are
        # masked — only the name and a hint are shown.
        from src.secret_store import get_secret, list_secret_names

        stored_names = list_secret_names()
        if stored_names:
            console.print("\n[cyan]Stored Keys (env):[/cyan]")
            for name in stored_names:
                value = get_secret(name) or ""
                masked = f"{value[:4]}...{value[-4:]}" if len(value) > 10 else "Set"
                console.print(f"    {name}: {masked}")

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
    # ch02 round-3 GAP B: warm the context memos while the (heavy)
    # src.repl import and REPL constructor run and the user types the
    # first prompt. The dispatch-level trust gate already ran, so the
    # system-context lane self-gates correctly. Mirrors TS
    # startDeferredPrefetches (main.tsx:392-439) post-render kick.
    from src.deferred_init import start_deferred_prefetches

    start_deferred_prefetches()

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


if __name__ == '__main__':
    sys.exit(main())

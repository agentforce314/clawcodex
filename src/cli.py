"""CLI entry point for Claw Codex."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ch02 round-4 WI-3 — nothing heavy at module scope. This module IS the
# process entry (`clawcodex = src.cli:main`), so anything fired here is
# paid by EVERY invocation including the fast paths (--version, mcp,
# doctor, agent-server-as-subcommand; the Ink client's spawned backend
# does NOT route here — it runs `python -m src.entrypoints.agent_server_cli`
# directly). TS's fast paths pay neither rich-equivalent imports nor
# the keychain/MDM spawns because they run before main.tsx is imported
# (cli.tsx:84-427 vs main.tsx:13-20). The keychain/MDM prefetch now fires
# in main() once the invocation is known to need the full pipeline; rich
# imports live in the functions that render with them. Consumers self-heal
# regardless — init.py awaits the same get-or-start singletons, which
# start the subprocess on first use if the early fire was skipped.
from src.prefetch import (
    get_or_start_keychain_prefetch,
    get_or_start_mdm_raw_read,
)


def main():
    """CLI main entry point."""
    # WI-0.1 (ch17 Phase 0): instrument cold-start phases. Env-gated by
    # ``CLAUDE_CODE_PROFILE_STARTUP``; a no-op import + no-op call when
    # disabled (~ns overhead). On exit the profiler writes a Markdown
    # report to ``$CLAUDE_CONFIG_DIR/startup-perf/{session_id}.txt``.
    from src.utils.startup_profiler import profile_checkpoint
    profile_checkpoint("cli_main_entry")

    import os

    # OpenClaude default: experimental API betas off unless the user opts in
    # (mirrors typescript/src/entrypoints/cli.tsx:44 — tool search
    # defer_loading / global cache scope / context management need internal
    # API support external accounts lack → 500). Per-process entry:
    # everything cli.main() spawns inherits this via the environment
    # (tui_launcher passes env=dict(os.environ)); the standalone
    # agent-server entry sets its own (agent_server_cli).
    os.environ.setdefault("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "true")

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
        if token == 'agent-server':
            from src.entrypoints.agent_server_cli import run_agent_server_subcommand
            return run_agent_server_subcommand(rest)
        if token == 'tui':
            return _run_tui_subcommand(rest)

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
    # WI-4.1 (ch17 Phase 4) as relocated by ch02 round-4 WI-3: fire the
    # keychain + MDM child processes the moment the invocation is known to
    # need the full pipeline (all fast paths have returned above). Popen
    # returns in microseconds; the subprocess work overlaps run_pre_action
    # → init(), whose consumer awaits these same singleton handles
    # (init.py:84-88). Non-macOS: no-op sentinels.
    get_or_start_keychain_prefetch()
    get_or_start_mdm_raw_read()

    profile_checkpoint("phase0_end_phase2_start")
    from src.init import run_pre_action
    run_pre_action(args)
    profile_checkpoint("phase2_end_phase3_start")

    # Resolve permission state ONCE here so all modes (print/TUI/REPL) honor
    # ``--dangerously-skip-permissions`` consistently. Mirrors
    # ``typescript/src/main.tsx:1383-1389``.
    _resolve_permission_state(args)
    profile_checkpoint("permissions_resolved")

    # Startup provider validation (ENTRY-2, port of cli.tsx:149 /
    # providerValidation.ts:479-528): surface a broken provider config NOW
    # instead of deep inside the first API call. TS split: non-interactive
    # exits, an interactive TTY warns and continues (the TUI can repair).
    # Fast paths returned above, so their cold-start cost is untouched. The
    # headless path re-runs the same shared helper internally — idempotent
    # defense-in-depth for direct run_headless callers, not an accident.
    from src.entrypoints.provider_validation import validate_provider_at_startup

    validate_provider_at_startup(
        args.provider,
        interactive=not args.print and sys.stdout.isatty(),
    )
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

    # Interactive path: the sole interactive UI is the TypeScript Ink TUI, which
    # spawns + owns a Python agent-server child (see
    # :mod:`src.entrypoints.tui_launcher`). The former in-process surfaces — the
    # opt-in Textual TUI (``src/tui/``) and the Rich/prompt_toolkit REPL
    # (``src/repl/``) — were removed in favor of this single, higher-fidelity
    # client. ``clawcodex tui`` is the explicit form (``_run_tui_subcommand``
    # runs this same bootstrap + trust gate).
    profile_checkpoint("mode_dispatch_tui")

    # Folder-trust gate — runs HERE in the Python parent, before we hand the TTY
    # to the Ink client, because the agent-server backend performs no trust gate
    # of its own. Already-trusted sessions (incl. ones seeded by run_pre_action)
    # skip the prompt.
    if not _gate_folder_trust():
        return 1

    profile_checkpoint("phase4_dispatch")
    from src.entrypoints.tui_launcher import launch_ink_tui

    return launch_ink_tui(
        provider=args.provider,
        model=args.model,
        permission_mode=args._resolved_permission_mode,
        is_bypass_available=args._resolved_is_bypass_available,
    )


def _gate_folder_trust() -> bool:
    """Run the folder-trust gate; return ``False`` only when an interactive user
    declines (the caller then exits 1).

    Shared by the default interactive entry and the ``clawcodex tui`` subcommand
    — both hand control to the tool-executing agent-server, which has no trust
    gate of its own, so the gate must run before either spawns it.
    """
    from src.services.startup_gates import check_trust_accepted

    if check_trust_accepted():
        return True
    if sys.stdin.isatty():
        return _prompt_folder_trust()
    # Non-TTY (piped) stdin: grant trust implicitly. The Ink TUI needs a real
    # TTY to render, so this path normally proceeds to a clean launch failure
    # rather than an interactive session — but trust is still established so the
    # project env is applied for any diagnostics.
    from src.permissions.trust_boundary import establish_session_trust

    establish_session_trust()
    return True


def _run_tui_subcommand(rest: list[str]) -> int:
    """``clawcodex tui`` — the explicit form of the default interactive entry.

    Unlike the lean ``mcp`` / ``doctor`` / ``daemon`` fast-paths, this launches
    the tool-executing agent-server, so it runs the SAME interactive bootstrap
    (``run_pre_action`` — config-env application + trust seeding) and
    folder-trust gate as the default ``clawcodex`` entry before handing off to
    the Ink client. Without this, ``clawcodex tui`` would reach the backend with
    no trust prompt and without the project env the child inherits.
    """
    from types import SimpleNamespace

    from src.init import run_pre_action

    # Full-pipeline path — fire the keychain/MDM prefetch here just like
    # main()'s parsed path does (ch02 round-4 WI-3): the sieve dispatched
    # before the post-sieve fire, and init() below awaits these handles.
    get_or_start_keychain_prefetch()
    get_or_start_mdm_raw_read()

    # ``print=False`` => treated as an interactive session by run_pre_action.
    run_pre_action(SimpleNamespace(print=False))

    # Startup provider validation — this path never reaches main()'s
    # dispatch-region call site (the sieve returned before argparse), so it
    # runs the same shared helper itself (critic P3). ``rest`` is parsed by
    # run_tui_launcher, so eager-parse --provider here (the TS
    # eagerParseCliFlag idiom, cli.tsx:159-160).
    from src.entrypoints.provider_validation import validate_provider_at_startup

    provider_flag = None
    for i, token in enumerate(rest):
        if token == "--provider" and i + 1 < len(rest):
            provider_flag = rest[i + 1]
        elif token.startswith("--provider="):
            provider_flag = token.split("=", 1)[1]
    validate_provider_at_startup(provider_flag, interactive=sys.stdout.isatty())

    if not _gate_folder_trust():
        return 1
    from src.entrypoints.tui_launcher import run_tui_launcher

    return run_tui_launcher(rest)


def _prompt_folder_trust() -> bool:
    """Plain-text folder-trust prompt (port of the C8 TrustFolderScreen).

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
  clawcodex                             Start the interactive Ink TUI
  clawcodex tui                         Start the interactive Ink TUI (explicit)
  clawcodex -p "hello"                  Non-interactive mode (text output)
  clawcodex -p "hi" --output-format json
  clawcodex -p --output-format stream-json --input-format stream-json < input.ndjson
""",
    )

    parser.add_argument('prompt', nargs='?', help='Prompt to send in non-interactive mode')
    parser.add_argument('--version', action='store_true', help='Show version information')
    parser.add_argument('--config', action='store_true', help='Show current configuration')

    # ---- Interactive UI ----
    #
    # The sole interactive UI is the TypeScript Ink TUI — ``clawcodex`` with no
    # mode flags, or the explicit ``clawcodex tui`` subcommand. It spawns + owns
    # a Python agent-server child (see :mod:`src.entrypoints.tui_launcher`). The
    # former in-process Textual TUI (``--tui``) and Rich REPL (``--legacy-repl`` /
    # ``--no-tui`` / ``--stream``) were removed in favor of this single client.

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
        '--fallback-model',
        type=str,
        default=None,
        dest='fallback_model',
        help='Model to switch to after repeated overloaded (529) errors '
             '(session-sticky; never persisted)',
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

    if args.fallback_model and args.fallback_model == args.model:
        # TS validates the same way (main.tsx:1339): a fallback equal to the
        # primary can never relieve capacity.
        print("error: --fallback-model must differ from --model", file=sys.stderr)
        return 2

    options = HeadlessOptions(
        prompt=args.prompt,
        output_format=args.output_format,
        input_format=args.input_format,
        provider_name=args.provider,
        model=args.model,
        fallback_model=args.fallback_model,
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


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]


def _show_provider_defaults_table() -> None:
    """Print a table showing available providers and their defaults."""
    from rich.console import Console
    from rich.table import Table

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
    from rich.console import Console
    from rich.prompt import Prompt

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
    from rich.console import Console

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


if __name__ == '__main__':
    sys.exit(main())

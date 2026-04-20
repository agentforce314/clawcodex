"""CLI entry point for Claw Codex."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table


def main():
    """CLI main entry point."""
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
    # subcommand. We only need to detect `login` / `config`; everything else
    # falls through to the main parser.
    argv = sys.argv[1:]
    for idx, token in enumerate(argv):
        if token.startswith('-'):
            continue
        if token in ('login', 'config'):
            rest = argv[idx + 1:]
            if token == 'login':
                return handle_login()
            return show_config()
        break

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from src import __version__
        print(f"claw-codex version {__version__} (Python)")
        return 0

    if args.config:
        return show_config()

    if args.print:
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
        return _run_tui_mode(args)

    return start_repl(stream=args.stream)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clawcodex",
        description="Claw Codex - Claude Code Python Implementation",
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
        '--dangerously-skip-permissions',
        dest='dangerously_skip_permissions',
        action='store_true',
        help='Bypass all tool permission checks (use with care)',
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
        help='Override the provider (anthropic, openai, glm, minimax)',
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
    console.print("\n[bold blue]Claw Codex - API Configuration[/bold blue]\n")

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


def start_repl(stream: bool = False):
    """Start interactive REPL."""
    from src.config import get_default_provider
    from src.repl import ClawcodexREPL

    provider = get_default_provider()
    repl = ClawcodexREPL(provider_name=provider, stream=stream)
    repl.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())

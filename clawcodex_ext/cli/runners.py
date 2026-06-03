"""Downstream CLI runners — owns mode helpers and patch-sensitive entrypoints."""

from __future__ import annotations


# ----------------------------------------------------------------------
# Print mode
# ----------------------------------------------------------------------

def run_print_mode(args) -> int:
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


# ----------------------------------------------------------------------
# TUI mode
# ----------------------------------------------------------------------

def run_tui_mode(args) -> int:
    """Boot the Textual-based interactive TUI (Phase 11)."""

    from clawcodex_ext.tui.entrypoint import run_tui
    from src.entrypoints.tui import TUIOptions

    allowed = _split_csv(args.allowed_tools)
    disallowed = _split_csv(args.disallowed_tools)

    # --resume without SESSION_ID means "browse" mode
    resume_val = getattr(args, 'resume', None)
    resume_session_id = None if resume_val == 'browse' else resume_val
    resume_browse = resume_val == 'browse'

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
    return run_tui(
        options,
        resume_session_id=resume_session_id,
        resume_browse=resume_browse,
    )


# ----------------------------------------------------------------------
# Utility helpers
# ----------------------------------------------------------------------

def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]


def _show_provider_defaults_table() -> None:
    """Print a table showing available providers and their defaults."""
    from src.providers import PROVIDER_INFO

    from rich.console import Console
    from rich.table import Table

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


# ----------------------------------------------------------------------
# Login / Config (compatibility patch targets)
# ----------------------------------------------------------------------

def handle_login() -> int:
    """Interactive provider credential configuration."""
    from rich.console import Console
    from rich.prompt import Prompt

    console = Console()
    console.print("\n[bold blue]ClawCodex - Provider Configuration[/bold blue]\n")

    _show_provider_defaults_table()

    from src.config import get_provider_config, set_api_key, set_default_provider
    from src.providers import PROVIDER_INFO
    provider_names = list(PROVIDER_INFO.keys())

    provider = Prompt.ask(
        "Select LLM provider",
        choices=provider_names,
        default="anthropic"
    )

    info = PROVIDER_INFO[provider]

    if provider == "openai-codex":
        from src.auth.codex_oauth import login_codex_device_flow

        login_codex_device_flow(console=console)
        current = get_provider_config(provider)
        console.print(f"\n[dim]Available models:[/dim] {', '.join(info['available_models'])}")
        console.print(f"[dim]Default:[/dim] [bold]{info['default_model']}[/bold]")
        default_model = Prompt.ask(
            f"{provider.upper()} Default Model",
            default=current.get("default_model") or info["default_model"],
        )
        set_api_key(
            provider,
            api_key="",
            base_url=current.get("base_url") or info["default_base_url"],
            default_model=default_model,
        )
        set_default_provider(provider)
        console.print("\n[green]OpenAI Codex login saved successfully![/green]")
        console.print(f"[green]Default provider set to: {provider}[/green]\n")
        return 0

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

    set_api_key(provider, api_key=api_key, base_url=base_url, default_model=default_model)
    set_default_provider(provider)

    console.print(f"\n[green]{provider.upper()} API Key saved successfully![/green]")
    console.print(f"[green]Default provider set to: {provider}[/green]\n")
    return 0


def show_config() -> int:
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
            console.print(f"\n  [yellow]{provider_name.upper()}:[/yellow]")
            if provider_name == "openai-codex":
                from src.auth.codex_oauth import get_codex_auth_status

                status = get_codex_auth_status()
                console.print(f"    Auth Mode: ChatGPT OAuth")
                console.print(f"    Authenticated: {'Yes' if status.is_authenticated else 'No'}")
                console.print(f"    Auth File: {status.auth_file}")
                if status.source:
                    console.print(f"    Source: {status.source}")
                if status.expires_at is not None:
                    console.print(f"    Expires At: {status.expires_at}")
                if status.error:
                    console.print(f"    Status: {status.error}")
            else:
                api_key = provider_config.get("api_key", "")
                masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "Not set"
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
    resume_session_id: str | None = None,
) -> int:
    """Start interactive REPL.

    ``permission_mode`` and ``is_bypass_permissions_mode_available`` are
    resolved by :func:`resolve_permission_state`. They control whether
    the in-process tool registry will short-circuit permission checks
    for the user (when ``--dangerously-skip-permissions`` is set).

    ``resume_session_id`` optionally loads a previous session by ID,
    so ``clawcodex --resume abc123`` continues that conversation.
    """
    from src.config import get_default_provider
    from clawcodex_ext.repl.app import ClawCodexExtREPL

    provider = get_default_provider()
    repl = ClawCodexExtREPL(
        provider_name=provider,
        stream=stream,
        permission_mode=permission_mode,
        is_bypass_permissions_mode_available=is_bypass_permissions_mode_available,
        resume_session_id=resume_session_id,
    )
    repl.run()
    return 0
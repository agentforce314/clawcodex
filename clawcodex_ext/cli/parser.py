"""Downstream CLI parser — owns build_parser()."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clawcodex",
        description="ClawCodex - Claude Code Python Implementation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  clawcodex --version                   Show version
  clawcodex login                       Configure API keys or ChatGPT OAuth
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
        help='Override the provider (anthropic, openai, openai-codex, glm, minimax, openrouter, deepseek)',
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
        '--resume',
        nargs='?',
        default=None,
        const='browse',
        metavar='SESSION_ID',
        help='Resume a previous session by session ID; without SESSION_ID, browse sessions',
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
        "  login    Configure API keys or ChatGPT OAuth (interactive)\n"
        "  config   Show current configuration\n"
    )
    return parser
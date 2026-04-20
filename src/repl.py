"""
Claw Codex - Interactive CLI

A complete reimplementation of Claude Code with interactive terminal interface.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

# Try to import rich for colored output
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.prompt import Prompt
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    Console = None  # type: ignore

from .commands import get_commands, get_command
from .tools import get_tools, get_tool
from .runtime import PortRuntime
from .port_manifest import build_port_manifest
from .query_engine import QueryEnginePort


from .parity_audit import run_parity_audit
from .setup import run_setup


from .bootstrap_graph import build_bootstrap_graph
from .command_graph import build_command_graph
from .tool_pool import assemble_tool_pool


class ClawcodexCLI:
    """Interactive CLI for Claw Codex."""

    def __init__(self):
        self.manifest = build_port_manifest()
        self.runtime = PortRuntime()
        self.running = True
        self.console = Console() if HAS_RICH else None

        # Color definitions for non-rich fallback
        self.colors = {
            'cyan': '\033[96m',
            'green': '\033[92m',
            'yellow': '\033[93m',
            'red': '\033[91m',
            'bold': '\033[1m',
            'reset': '\033[0m',
        }

    def _colorize(self, text: str, color: str) -> str:
        """Apply color to text."""
        if HAS_RICH:
            return text
        return f"{self.colors.get(color, '')}{text}{self.colors['reset']}"

    def print_banner(self):
        """Print welcome banner."""
        if HAS_RICH:
            self.console.print(Panel.fit(
                "[bold cyan]Claw Codex[/bold cyan]\n"
                "[dim]A complete reimplementation of Claude Code[/dim]",
                subtitle="Interactive Mode • Type 'help' for commands",
                border_style="round",
            ))
        else:
            banner = f"""
{self._colorize('╔═══════════════════════════════════════════════════════════╔', 'cyan')}
{self._colorize('║', 'cyan')}   {self._colorize('Claw Codex', 'bold')} - Claude Code Reimplementation   {self._colorize('║', 'cyan')}
{self._colorize('║', 'cyan')}   Type "help" for commands • Interactive Mode                  {self._colorize('║', 'cyan')}
{self._colorize('╚═══════════════════════════════════════════════════════════╛', 'cyan')}
"""
            print(banner)

    def print_status(self):
        """Print current project status."""
        if HAS_RICH:
            table = Table(title="Project Status", show_header=True)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="green")
            table.add_row("Python Files", str(self.manifest.total_python_files))
            table.add_row("Commands", "207")
            table.add_row("Tools", "150+")
            table.add_row("Subsystems", str(len(self.manifest.top_level_modules)))
            self.console.print(table)
        else:
            print(f"\n{self._colorize('Project Status:', 'bold')}")
            print(f"  Python Files:  {self._colorize(str(self.manifest.total_python_files), 'green')}")
            print(f"  Commands:     {self._colorize('207', 'green')}")
            print(f"  Tools:       {self._colorize('150+', 'green')}")
            print(f"  Subsystems:   {self._colorize(str(len(self.manifest.top_level_modules)), 'green')}")
            print()

    def print_help(self):
        """Print available commands."""
        commands = [
            ("help", "Show this help message"),
            ("status", "Show project status"),
            ("summary", "Show porting summary"),
            ("commands [query]", "List/search commands"),
            ("tools [query]", "List/search tools"),
            ("route <prompt>", "Route prompt to commands/tools"),
            ("bootstrap <prompt>", "Start a session"),
            ("audit", "Run parity audit"),
            ("exit", "Exit interactive mode"),
        ]

        if HAS_RICH:
            table = Table(title="Available Commands", show_header=True)
            table.add_column("Command", style="cyan")
            table.add_column("Description", style="white")
            for cmd, desc in commands:
                table.add_row(cmd, desc)
            self.console.print(table)
        else:
            print(f"\n{self._colorize('Available Commands:', 'bold')}")
            for cmd, desc in commands:
                print(f"  {self._colorize(cmd, 'cyan'):20} {desc}")
            print()

    def handle_command(self, user_input: str) -> bool:
        """Handle user command. Returns True to continue, False to exit."""
        parts = user_input.strip().split(maxsplit=1)
        if not parts:
            return True

        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if cmd in ('exit', 'quit', 'q'):
            print(f"\n{self._colorize('Goodbye!', 'green')} 👋\n")
            return False

        elif cmd == 'help':
            self.print_help()
        elif cmd == 'status':
            self.print_status()
        elif cmd == 'summary':
            print()
            print(QueryEnginePort(self.manifest).render_summary())
        elif cmd == 'audit':
            print()
            print(run_parity_audit().to_markdown())
        elif cmd == 'commands':
            query = args[0] if args else None
            if query:
                from .commands import render_command_index
                print()
                print(render_command_index(limit=20, query=query))
            else:
                cmds = get_commands()
                print(f"\nCommand entries: {len(cmds)}")
                for c in cmds[:20]:
                    print(f"  - {c.name}")
                if len(cmds) > 20:
                    print(f"  ... and {len(cmds) - 20} more")
        elif cmd == 'tools':
            query = args[0] if args else None
            if query:
                from .tools import render_tool_index
                print()
                print(render_tool_index(limit=20, query=query))
            else:
                tools = get_tools()
                print(f"\nTool entries: {len(tools)}")
                for t in tools[:20]:
                    print(f"  - {t.name}")
                if len(tools) > 20:
                    print(f"  ... and {len(tools) - 20} more")
        elif cmd == 'route':
            if not args:
                print(f"{self._colorize('Error:', 'red')} Please provide a prompt to route")
            else:
                prompt = ' '.join(args)
                matches = self.runtime.route_prompt(prompt, limit=5)
                print(f"\n{self._colorize('Routed Matches:', 'bold')}")
                for m in matches:
                    print(f"  [{m.kind}] {m.name} (score: {m.score})")
        elif cmd == 'bootstrap':
            if not args:
                print(f"{self._colorize('Error:', 'red')} Please provide a prompt")
            else:
                prompt = ' '.join(args)
                print(f"\n{self._colorize('Starting session...', 'yellow')}")
                session = self.runtime.bootstrap_session(prompt, limit=5)
                print(session.as_markdown())
        elif cmd == 'show':
            if not args:
                print(f"{self._colorize('Usage:', 'yellow')} show <command|tool> <name>")
            else:
                kind = args[0]
                name = ' '.join(args[1:]) if len(args) > 1 else None
                if not name:
                    print(f"{self._colorize('Usage:', 'yellow')} show <command|tool> <name>")
                    return True
                if kind == 'command':
                    module = get_command(name)
                    if module:
                        print(f"\n{module.name}")
                        print(f"  Source: {module.source_hint}")
                        print(f"  {module.responsibility}")
                    else:
                        print(f"{self._colorize('Command not found:', 'red')} {name}")
                elif kind == 'tool':
                    module = get_tool(name)
                    if module:
                        print(f"\n{module.name}")
                        print(f"  Source: {module.source_hint}")
                        print(f"  {module.responsibility}")
                    else:
                        print(f"{self._colorize('Tool not found:', 'red')} {name}")
                else:
                    print(f"{self._colorize('Unknown type:', 'red')} {kind}")
        else:
            # Treat as natural language prompt - route and suggest
            print(f"\n{self._colorize('Routing your request...', 'yellow')}")
            matches = self.runtime.route_prompt(user_input, limit=5)
            if matches:
                print(f"{self._colorize('Suggested commands/tools:', 'bold')}")
                for m in matches:
                    print(f"  [{m.kind}] {m.name} (score: {m.score})")
                print(f"\n{self._colorize('Use:', 'cyan')} show command <name> or show tool <name> for details")
            else:
                print(f"{self._colorize('No matching commands or tools found.', 'yellow')}")

        return True

    def run(self):
        """Run the interactive CLI."""
        self.print_banner()
        self.print_status()
        print("\n" + self._colorize('Type your request or "help" for commands:', 'cyan'))
        print(f"{self._colorize('─' * 60, 'dim')}\n")

        while self.running:
            try:
                user_input = input(f"{self._colorize('clawcodex>', 'cyan')} ").strip()
                if user_input:
                    self.running = self.handle_command(user_input)
            except (KeyboardInterrupt, EOFError):
                print(f"\n{self._colorize('Goodbye!', 'green')} 👋\n")
                break


def main():
    """Main entry point."""
    cli = ClawcodexCLI()
    cli.run()


if __name__ == '__main__':
    main()

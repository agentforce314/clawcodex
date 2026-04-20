"""Startup header widget — the Claw Codex banner.

Port of :meth:`src.repl.core.ClawcodexREPL._print_startup_header` rendered
inside a Textual ``Static`` so the banner becomes a first-class, persistent
component of the TUI (matching the Ink ``<Header>`` in
``typescript/src/screens/REPL.tsx``) instead of a one-shot ``print`` at
startup.
"""

from __future__ import annotations

from pathlib import Path

from rich.align import Align
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.widgets import Static


_MASCOT = "\n".join([
    "  /\\__/\\",
    " / o  o \\",
    "(  __  )",
    " \\/__/  ",
])


def _display_cwd(workspace_root: Path) -> str:
    try:
        home = Path.home()
        rel = workspace_root.relative_to(home)
        return f"~/{rel}" if str(rel) != "." else "~"
    except Exception:
        return str(workspace_root)


def _truncate_middle(text: str, max_width: int) -> str:
    if max_width <= 0 or len(text) <= max_width:
        return text
    if max_width <= 3:
        return text[:max_width]
    keep = max_width - 1
    head = keep // 2
    tail = keep - head
    return text[:head] + "…" + text[-tail:]


class StartupHeader(Static):
    """Fixed banner at the top of the TUI.

    Re-uses the Rich layout from the legacy REPL so the visual identity
    stays consistent between the two interactive stacks.
    """

    DEFAULT_CSS = """
    StartupHeader {
        height: auto;
        padding: 0 0;
        background: $background;
    }
    """

    def __init__(
        self,
        *,
        version: str,
        model: str,
        provider: str,
        workspace_root: Path,
        slash_hints: str = "/help  •  /tools  •  /stream  •  /render-last  •  /exit",
        width_hint: int | None = None,
    ) -> None:
        self._version = version
        self._model = model
        self._provider = provider
        self._workspace_root = Path(workspace_root)
        self._slash_hints = slash_hints
        self._width_hint = width_hint
        super().__init__(self._render_banner(), markup=False)

    def refresh_banner(self, *, model: str | None = None, provider: str | None = None) -> None:
        """Update model/provider labels live (e.g. after `/model ...`)."""
        if model is not None:
            self._model = model
        if provider is not None:
            self._provider = provider
        self.update(self._render_banner())

    def _render_banner(self) -> Panel:
        display_path = _display_cwd(self._workspace_root)
        width = self._width_hint or 80
        content_width = max(28, min(width - 12, 72))
        table = Table.grid(padding=(0, 1))
        table.add_column(style="bright_black", justify="right", no_wrap=True)
        table.add_column(style="white", ratio=1)
        table.add_row(
            "Version",
            Text.assemble(
                ("Claw Codex", "bold white"),
                ("  ", ""),
                (f"v{self._version}", "bold cyan"),
            ),
        )
        table.add_row("Model", Text(self._model or "unknown", style="bold magenta"))
        table.add_row(
            "Provider",
            Text(f"{self._provider.upper()} Provider", style="bold green"),
        )
        table.add_row(
            "Workspace",
            Text(_truncate_middle(display_path, content_width - 12), style="bold blue"),
        )

        footer = Text(self._slash_hints, style="dim")
        mascot_block = Text(_MASCOT, style="bold orange3", no_wrap=True)
        body = Group(
            Columns([mascot_block, table], align="center", expand=False),
            Text(""),
            Align.center(footer),
        )
        return Panel(
            body,
            border_style="bright_black",
            title="[bold bright_cyan] CLAW CODEX [/bold bright_cyan]",
            subtitle="[dim]interactive terminal[/dim]",
            padding=(0, 2),
        )

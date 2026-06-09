"""REPL session browser — terminal-based session picker for ``--resume`` (no ID).

Replaces the TUI-only ``ResumeConversation`` screen when the REPL is in use.
Lets the user browse, filter, search content, and pick a previous session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from rich.console import Console
from rich.table import Table

from src.services.session_storage import SessionMetadata, SessionStorage


def browse_sessions_interactive(
    console: Console | None = None,
) -> str | None:
    """Show an interactive session browser in the terminal.

    Lists sessions from :class:`SessionStorage` and lets the user:
    - Enter a session ID (or prefix) to resume
    - Enter ``#<number>`` to pick by list index
    - Enter ``/search <text>`` to search within session content
    - Enter ``/show <num>`` to show full session ID for a row
    - Press Enter with empty input to cancel

    Returns the selected ``session_id`` or ``None`` if cancelled.
    """
    if console is None:
        console = Console()

    metas = SessionStorage.list_sessions(limit=50)
    if not metas:
        console.print("[yellow]No past sessions found.[/yellow]")
        return None

    # Display session table
    _render_session_table(console, metas)
    _print_help(console)

    # Interactive selection
    while True:
        try:
            raw = input(f"\n❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return None

        if not raw:
            return None

        # Help
        if raw == "/help":
            _print_help(console)
            continue

        # Content search: /search <query>
        if raw.startswith("/search "):
            query = raw[8:].strip()
            if not query:
                console.print("[yellow]Usage: /search <text>[/yellow]")
                continue
            results = _search_session_content(metas, query)
            if not results:
                console.print(f"[yellow]No sessions contain '{query}'.[/yellow]")
            else:
                console.print(f"[green]Sessions matching '{query}':[/green]")
                _render_session_table(console, results)
                metas = list(results)  # Update working list for subsequent picks
            continue

        # Show full session ID: /show <num>
        if raw.startswith("/show "):
            try:
                idx = int(raw[6:].strip()) - 1
                if 0 <= idx < len(metas):
                    console.print(f"[dim]Full session ID: {metas[idx].session_id}[/dim]")
                else:
                    console.print("[red]Index out of range.[/red]")
            except ValueError:
                console.print("[red]Usage: /show <number>[/red]")
            continue

        # Numeric selection: #<number>
        if raw.startswith("#"):
            try:
                idx = int(raw[1:])
                if 1 <= idx <= len(metas):
                    sid = metas[idx - 1].session_id
                    console.print(f"[green]Selected session: {sid[:8]}…[/green]")
                    return sid
            except ValueError:
                pass
            console.print("[red]Invalid selection. Use # followed by a number from the table.[/red]")
            continue

        # Prefix match against session ID
        matches = [
            m for m in metas
            if m.session_id.startswith(raw) or raw in m.session_id
        ]
        if len(matches) == 1:
            sid = matches[0].session_id
            console.print(f"[green]Selected session: {sid[:8]}…[/green]")
            return sid
        elif len(matches) > 1:
            console.print("[yellow]Multiple matches — be more specific:[/yellow]")
            _render_session_table(console, matches)
            metas = list(matches)
            _print_help(console)
            continue
        else:
            # No ID match — try content search as fallback
            content_results = _search_session_content(metas, raw)
            if content_results:
                console.print(f"[green]No session ID match. Showing sessions whose content matches '{raw}':[/green]")
                _render_session_table(console, content_results)
                metas = list(content_results)
                _print_help(console)
            else:
                console.print(
                    "[red]No matches. Enter a session ID (or prefix), #<number>, "
                    "/search <text>, or press Enter to cancel.[/red]"
                )
            continue


def _search_session_content(
    metas: Sequence[SessionMetadata],
    query: str,
) -> list[SessionMetadata]:
    """Search session transcript content for ``query``.

    Returns the subset of *metas* whose transcript text contains *query*
    (case-insensitive). Falls back to searching metadata fields if loading
    the transcript fails.
    """
    query_lower = query.lower()
    results: list[SessionMetadata] = []
    for meta in metas:
        # First check metadata fields (fast path)
        if query_lower in (meta.last_user_input or "").lower():
            results.append(meta)
            continue
        if query_lower in (meta.title or "").lower():
            results.append(meta)
            continue

        # Slow path: load transcript text
        try:
            storage = SessionStorage(session_id=meta.session_id)
            messages = storage.read_messages()
            found = False
            for msg in messages:
                content = getattr(msg, "content", None) or ""
                if isinstance(content, str) and query_lower in content.lower():
                    found = True
                    break
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, str) and query_lower in item.lower():
                            found = True
                            break
                        elif isinstance(item, dict):
                            text = item.get("text") or item.get("content") or ""
                            if isinstance(text, str) and query_lower in text.lower():
                                found = True
                                break
                    if found:
                        break
            if found:
                results.append(meta)
        except Exception:
            # Skip sessions that can't be loaded
            pass

    return results


def load_session_metadata_for_display(limit: int = 50) -> list[dict]:
    """Load session metadata for display.

    Returns a list of dicts with ``session_id``, ``time``, ``model``,
    ``message_count``, ``last_user_input``, ``title``.
    """
    metas = SessionStorage.list_sessions(limit=limit)
    out: list[dict] = []
    for meta in metas:
        ts = ""
        if meta.last_updated:
            try:
                ts = datetime.fromtimestamp(meta.last_updated).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except Exception:
                ts = str(meta.last_updated)
        last_input = (meta.last_user_input or "")[:60]
        if len(last_input) == 60:
            last_input += "…"
        out.append({
            "session_id": meta.session_id,
            "time": ts,
            "model": meta.model or "",
            "message_count": meta.message_count,
            "last_user_input": last_input,
            "title": meta.title or "",
        })
    return out


# ---- internal helpers ----

def _render_session_table(console: Console, metas: Sequence[SessionMetadata]) -> None:
    """Render a table of sessions to the console."""
    table = Table(title="Past sessions", show_lines=False)
    table.add_column("#", style="dim", no_wrap=True)
    table.add_column("Session ID", style="bold cyan")
    table.add_column("Time", style="green")
    table.add_column("Last input", style="white", max_width=60)
    table.add_column("Model", style="dim", max_width=20)
    table.add_column("Msgs", style="dim", justify="right")

    for idx, meta in enumerate(metas, start=1):
        ts = ""
        if meta.last_updated:
            try:
                ts = datetime.fromtimestamp(meta.last_updated).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except Exception:
                ts = str(meta.last_updated)

        last_input = (meta.last_user_input or "")[:57]
        if len(last_input) == 57:
            last_input += "…"

        sid_short = meta.session_id[:8] + "…" if len(meta.session_id) > 10 else meta.session_id

        table.add_row(
            str(idx),
            sid_short,
            ts,
            last_input or "(no input)",
            meta.model or "",
            str(meta.message_count) if meta.message_count else "",
        )

    console.print(table)


def _print_help(console: Console) -> None:
    """Print help hints."""
    console.print(
        "[dim]Enter a session ID (or prefix) to resume · "
        "#<num> to pick by number · "
        "/search <text> to search content · "
        "/show <num> for full ID · "
        "Enter to cancel[/dim]"
    )


__all__ = [
    "browse_sessions_interactive",
    "load_session_metadata_for_display",
]

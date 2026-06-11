"""Global-search and quick-open modal screens (components C5).

Degraded ports of TS ``components/GlobalSearchDialog.tsx`` (ripgrep
content search → pick a match) and ``QuickOpenDialog.tsx`` (fuzzy file
open). Selection dismisses with the TS-verbatim PROMPT INSERTION string
(``@file#Lline `` / ``@path ``) — the app appends it to the composer
draft. The ``@file#Lline`` mention attaches the FILE downstream
(``expand_at_mentions`` strips the fragment; range slicing is a noted
follow-up). Esc dismisses with ``None``.

Keyboard model = the house ``HistorySearchScreen`` idiom: the Input
keeps focus the whole time; up/down/ctrl+p/n route to the
:class:`SelectList`; Enter selects the highlighted row.

Divergences (documented): no preview pane and no open-in-editor action
(no editor-spawn analog — the `/memory` decision); reached via
``/search`` and ``/open`` rather than the TS ctrl+shift chords
(terminals commonly swallow them; revisit with the keybindings phase);
content search re-queries on Enter rather than per-keystroke streaming
(bounded subprocess churn).
"""

from __future__ import annotations

from typing import Iterator

from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Input, Static

from src.services.workspace_search import (
    ContentMatch,
    file_insertion,
    filter_files,
    list_workspace_files,
    search_content,
)
from src.tui.widgets.select_list import SelectList, SelectOption
from src.utils.abort_controller import AbortController

from .dialog_base import DialogScreen


class GlobalSearchScreen(DialogScreen[str | None]):
    """Workspace content search; dismisses with a prompt insertion."""

    title_text = "Search workspace"
    footer_hint = "Enter searches / selects · ↑↓ navigate · Esc closes"

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("up", "move(-1)", "Previous", show=False),
        Binding("down", "move(1)", "Next", show=False),
        Binding("ctrl+p", "move(-1)", "Previous", show=False),
        Binding("ctrl+n", "move(1)", "Next", show=False),
    ]

    def __init__(self, cwd: str, initial_query: str = "") -> None:
        super().__init__()
        self._cwd = cwd
        self._initial_query = initial_query
        self._matches: list[ContentMatch] = []
        self._last_query: str | None = None
        self._abort: AbortController | None = None
        self._input: Input | None = None
        self._list: SelectList | None = None
        self._count_label: Static | None = None

    def build_body(self) -> Iterator[Widget]:
        self._input = Input(
            value=self._initial_query,
            placeholder="Search text… (enter to run)",
        )
        yield self._input
        self._count_label = Static(Text(""), markup=False)
        yield self._count_label
        self._list = SelectList([])
        yield self._list

    def _post_mount(self) -> None:
        if self._input is not None:
            self._input.focus()
        if self._initial_query.strip():
            self._run_search(self._initial_query)

    # ---- input handling ----
    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return  # stay idle on empty Enter (review m12)
        # Enter on an UNCHANGED query selects the highlighted match (the
        # HistorySearch flow); a changed query re-runs the search.
        if query == self._last_query and self._matches:
            self.action_select_current()
            return
        self._run_search(query)

    def action_move(self, delta: int) -> None:
        if self._list is not None:
            self._list.action_move(delta)

    def action_select_current(self) -> None:
        if self._list is None or self._list.current is None:
            return
        try:
            index = int(self._list.current.value)
            match = self._matches[index]
        except (TypeError, ValueError, IndexError):
            return
        self.dismiss(match.insertion())

    # ---- search ----
    def _run_search(self, query: str) -> None:
        # Stale-result guard (review B1): drop the previous matches NOW
        # so Enter-during-flight cannot select results of an older query.
        self._matches = []
        self._last_query = query.strip()
        if self._abort is not None:
            self._abort.abort("superseded")
        self._abort = AbortController()
        self._set_placeholder("searching…")
        self._search_worker(query.strip(), self._abort)

    @work(thread=True, group="global-search", exit_on_error=False)
    def _search_worker(self, query: str, abort: AbortController) -> None:
        from src.tool_system.utils.ripgrep import RipgrepAbortedError

        try:
            matches, truncated = search_content(
                query, self._cwd, abort_signal=abort.signal
            )
            error: str | None = None
        except RipgrepAbortedError:
            # Superseded or dialog-closed search: never report (a
            # same-query restart would otherwise flash "search failed:
            # …cancelled…" — review note).
            return
        except Exception as exc:
            matches, truncated, error = [], False, str(exc)
        try:
            self.app.call_from_thread(
                self._apply_results, query, matches, truncated, error
            )
        except Exception:
            pass  # app/screen tearing down

    def _apply_results(
        self,
        query: str,
        matches: list[ContentMatch],
        truncated: bool,
        error: str | None,
    ) -> None:
        if not self.is_attached:
            return
        if query != self._last_query:
            return  # superseded (review B1 second variant)
        if error:
            self._matches = []
            self._set_placeholder(f"search failed: {error}")
            return
        self._matches = matches
        if self._list is None:
            return
        options = [
            SelectOption(
                label=match.label(),
                value=str(i),
                description=match.text[:80],
            )
            for i, match in enumerate(matches)
        ]
        self._list.set_options(options)
        if self._count_label is not None:
            if not matches:
                note = "no matches"
            else:
                note = f"{len(matches)} match{'es' if len(matches) != 1 else ''}"
                if truncated:
                    note += "+ (truncated — refine the query)"
            self._count_label.update(Text(f"  {note}", style="dim"))

    def _set_placeholder(self, text: str) -> None:
        if self._list is not None:
            self._list.set_options([])
        if self._count_label is not None:
            self._count_label.update(Text(f"  {text}", style="dim"))

    def on_unmount(self) -> None:
        if self._abort is not None:
            self._abort.abort("dialog closed")


class QuickOpenScreen(DialogScreen[str | None]):
    """Fuzzy file finder; dismisses with an ``@path `` insertion."""

    title_text = "Quick open"
    footer_hint = "Enter inserts @path · ↑↓ navigate · Esc closes"

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("up", "move(-1)", "Previous", show=False),
        Binding("down", "move(1)", "Next", show=False),
        Binding("ctrl+p", "move(-1)", "Previous", show=False),
        Binding("ctrl+n", "move(1)", "Next", show=False),
    ]

    def __init__(self, cwd: str) -> None:
        super().__init__()
        self._cwd = cwd
        self._all_files: list[str] = []
        self._files_truncated = False
        self._shown: list[str] = []
        self._abort: AbortController | None = None
        self._input: Input | None = None
        self._list: SelectList | None = None
        self._count_label: Static | None = None

    def build_body(self) -> Iterator[Widget]:
        self._input = Input(placeholder="Type to search files…")
        yield self._input
        self._count_label = Static(Text("  loading files…", style="dim"), markup=False)
        yield self._count_label
        self._list = SelectList([])
        yield self._list

    def _post_mount(self) -> None:
        if self._input is not None:
            self._input.focus()
        self._abort = AbortController()
        self._load_files_worker(self._abort)

    @work(thread=True, exclusive=True, group="quick-open", exit_on_error=False)
    def _load_files_worker(self, abort: AbortController) -> None:
        try:
            files, truncated = list_workspace_files(
                self._cwd, abort_signal=abort.signal
            )
            error: str | None = None
        except Exception as exc:
            files, truncated, error = [], False, str(exc)
        try:
            self.app.call_from_thread(self._files_loaded, files, truncated, error)
        except Exception:
            pass

    def _files_loaded(
        self, files: list[str], truncated: bool, error: str | None
    ) -> None:
        if not self.is_attached:
            return
        if error:
            if self._count_label is not None:
                self._count_label.update(
                    Text(f"  file listing failed: {error}", style="dim")
                )
            return
        self._all_files = files
        self._files_truncated = truncated
        self._refilter(self._input.value if self._input else "")

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._all_files:
            self._refilter(event.value)

    def _refilter(self, query: str) -> None:
        self._shown = filter_files(self._all_files, query)
        if self._list is not None:
            self._list.set_options(
                [
                    SelectOption(label=path, value=str(i))
                    for i, path in enumerate(self._shown)
                ]
            )
        if self._count_label is not None:
            note = (
                f"{len(self._shown)} / {len(self._all_files)} files"
                if self._shown
                else "no files"
            )
            if self._files_truncated:
                note += " (file list truncated at 5000)"
            self._count_label.update(Text(f"  {note}", style="dim"))

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.action_select_current()

    def action_move(self, delta: int) -> None:
        if self._list is not None:
            self._list.action_move(delta)

    def action_select_current(self) -> None:
        if self._list is None or self._list.current is None:
            return
        try:
            path = self._shown[int(self._list.current.value)]
        except (TypeError, ValueError, IndexError):
            return
        self.dismiss(file_insertion(path))

    def on_unmount(self) -> None:
        if self._abort is not None:
            self._abort.abort("dialog closed")


__all__ = ["GlobalSearchScreen", "QuickOpenScreen"]

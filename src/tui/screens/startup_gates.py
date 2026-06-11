"""Startup security-gate dialogs (components C8).

Three boot-time screens, TS-faithful copy and outcomes:

* :class:`TrustFolderScreen` — TS ``TrustDialog.tsx``; dismisses
  ``"trust"`` or ``"exit"`` (Esc = exit too: the gate cannot be
  bypassed by closing the dialog).
* :class:`ExternalIncludesScreen` — TS
  ``ClaudeMdExternalIncludesDialog.tsx``; dismisses ``"yes"`` or
  ``"no"``; Esc maps to ``"no"`` (TS handleEscape → selection "no").
* :class:`BypassPermissionsScreen` — TS
  ``BypassPermissionsModeDialog.tsx``; dismisses ``"accept"``,
  ``"decline"`` (exit 1) or ``"escape"`` (TS _temp2 →
  gracefulShutdownSync(0)).
"""

from __future__ import annotations

from typing import Iterator

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from src.tui.widgets.select_list import SelectList, SelectOption

from .dialog_base import DialogScreen


class TrustFolderScreen(DialogScreen[str]):
    """First-launch folder trust gate."""

    title_text = "Accessing workspace:"
    footer_hint = "Enter selects · Esc exits"
    border_variant = "warning"

    def __init__(self, folder: str, warnings: list[str] | None = None) -> None:
        super().__init__()
        self._folder = folder
        self._warnings = list(warnings or [])
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        yield Static(Text(self._folder, style="bold"), markup=False)
        yield Static(
            Text(
                "Quick safety check: Is this a project you created or one "
                "you trust? (Like your own code, a well-known open source "
                "project, or work from your team). If not, take a moment "
                "to review what's in this folder first.",
                style="dim",
            ),
            markup=False,
        )
        for warning in self._warnings:
            yield Static(Text(f"  ! {warning}", style="yellow"), markup=False)
        self._select = SelectList(
            [
                SelectOption(label="Yes, I trust this folder", value="trust"),
                SelectOption(label="No, exit", value="exit"),
            ],
            allow_cancel=True,
        )
        yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    def on_select_list_option_selected(
        self, event: SelectList.OptionSelected
    ) -> None:
        self.dismiss(str(event.option.value))

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        # Trust cannot be deferred — closing the dialog is declining.
        self.dismiss("exit")


class ExternalIncludesScreen(DialogScreen[str]):
    """CLAUDE.md external @imports approval (asked once per project)."""

    title_text = "Allow external CLAUDE.md file imports?"
    footer_hint = "Enter selects · Esc disables"
    border_variant = "warning"

    def __init__(self, external_paths: list[str]) -> None:
        super().__init__()
        self._external_paths = list(external_paths)
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        yield Static(
            Text(
                "This project's CLAUDE.md imports files outside the "
                "current working directory. Never allow this for "
                "third-party repositories.",
                style="dim",
            ),
            markup=False,
        )
        if self._external_paths:
            listing = Text("External imports:\n", style="dim")
            for path in self._external_paths[:8]:
                listing.append(f"  {path}\n", style="dim")
            if len(self._external_paths) > 8:
                listing.append(
                    f"  … and {len(self._external_paths) - 8} more\n",
                    style="dim",
                )
            yield Static(listing, markup=False)
        self._select = SelectList(
            [
                SelectOption(
                    label="Yes, allow external imports", value="yes"
                ),
                SelectOption(
                    label="No, disable external imports", value="no"
                ),
            ],
            allow_cancel=True,
        )
        yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    def on_select_list_option_selected(
        self, event: SelectList.OptionSelected
    ) -> None:
        self.dismiss(str(event.option.value))

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        # TS handleEscape selects "no" (persisted decline).
        self.dismiss("no")


class BypassPermissionsScreen(DialogScreen[str]):
    """Bypass-permissions acceptance gate."""

    title_text = "WARNING: running in Bypass Permissions mode"
    footer_hint = "Enter selects · Esc exits"
    border_variant = "error"

    def __init__(self) -> None:
        super().__init__()
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        yield Static(
            Text(
                "In Bypass Permissions mode, the agent will not ask for "
                "your approval before running potentially dangerous "
                "commands.\nThis mode should only be used in a sandboxed "
                "container/VM that has restricted internet access and can "
                "easily be restored if damaged.",
                style="dim",
            ),
            markup=False,
        )
        yield Static(
            Text(
                "By proceeding, you accept all responsibility for actions "
                "taken while running in Bypass Permissions mode.",
            ),
            markup=False,
        )
        self._select = SelectList(
            [
                SelectOption(label="No, exit", value="decline"),
                SelectOption(label="Yes, I accept", value="accept"),
            ],
            allow_cancel=True,
        )
        yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    def on_select_list_option_selected(
        self, event: SelectList.OptionSelected
    ) -> None:
        self.dismiss(str(event.option.value))

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        # TS: Esc on this dialog exits with code 0 (distinct from the
        # explicit "No, exit" which exits 1).
        self.dismiss("escape")


__all__ = [
    "BypassPermissionsScreen",
    "ExternalIncludesScreen",
    "TrustFolderScreen",
]

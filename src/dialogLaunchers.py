from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DialogLauncher:
    name: str
    description: str


DEFAULT_DIALOGS = (
    DialogLauncher('summary', 'Launch the Markdown summary view'),
    DialogLauncher('parity_audit', 'Launch the parity audit view'),
)

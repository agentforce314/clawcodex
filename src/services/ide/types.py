"""IDE integration protocol types.

Mirrors TypeScript ide/types.ts — defines the contract between Claude Code
and IDE extensions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class IDEType(str, Enum):
    VSCODE = "vscode"
    JETBRAINS = "jetbrains"
    VIM = "vim"
    EMACS = "emacs"
    UNKNOWN = "unknown"


class IDEDiagnosticSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    HINT = "hint"


@dataclass
class IDERange:
    """Line/column range in a file."""
    start_line: int
    start_character: int
    end_line: int
    end_character: int


@dataclass
class IDESelection:
    """Current editor selection from the IDE."""
    file_path: str
    text: str
    range: IDERange
    language_id: str = ""


@dataclass
class IDEDiagnostic:
    """A diagnostic (error/warning) from the IDE's language server."""
    file_path: str
    message: str
    severity: IDEDiagnosticSeverity = IDEDiagnosticSeverity.ERROR
    range: IDERange | None = None
    source: str = ""
    code: str | int | None = None


@dataclass
class IDEConnection:
    """Represents a live connection to an IDE extension."""
    ide_type: IDEType = IDEType.UNKNOWN
    version: str = ""
    workspace_root: str = ""
    connected: bool = False
    capabilities: dict[str, bool] = field(default_factory=dict)

    @property
    def supports_selection(self) -> bool:
        return self.capabilities.get("selection", False)

    @property
    def supports_diagnostics(self) -> bool:
        return self.capabilities.get("diagnostics", False)

    @property
    def supports_open_file(self) -> bool:
        return self.capabilities.get("openFile", False)

    @property
    def supports_apply_edit(self) -> bool:
        return self.capabilities.get("applyEdit", False)

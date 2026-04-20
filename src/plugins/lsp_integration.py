from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .types import LoadedPlugin, PluginManifest

logger = logging.getLogger(__name__)


class DiagnosticSeverity(Enum):
    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4


@dataclass
class LspDiagnostic:
    file_path: str
    line: int
    column: int
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    source: str = ""
    code: str | None = None


@dataclass
class LspServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    language_ids: list[str] = field(default_factory=list)
    root_uri: str = ""
    initialization_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class LspPluginWrapper:
    plugin: LoadedPlugin
    server_config: LspServerConfig
    connected: bool = False
    diagnostics: list[LspDiagnostic] = field(default_factory=list)


_lsp_plugins: dict[str, LspPluginWrapper] = {}


def wrap_lsp_server_as_plugin(
    config: LspServerConfig,
    *,
    description: str = "",
) -> LspPluginWrapper:
    manifest = PluginManifest(
        name=f"lsp-{config.name}",
        description=description or f"LSP server: {config.name}",
        version="1.0.0",
    )

    plugin = LoadedPlugin(
        name=manifest.name,
        manifest=manifest,
        source=f"lsp:{config.name}",
        enabled=True,
    )

    wrapper = LspPluginWrapper(
        plugin=plugin,
        server_config=config,
    )

    _lsp_plugins[config.name] = wrapper
    return wrapper


def get_lsp_plugin(name: str) -> LspPluginWrapper | None:
    return _lsp_plugins.get(name)


def get_all_lsp_plugins() -> list[LspPluginWrapper]:
    return list(_lsp_plugins.values())


def add_diagnostics(
    server_name: str,
    diagnostics: list[LspDiagnostic],
) -> None:
    wrapper = _lsp_plugins.get(server_name)
    if wrapper is None:
        return
    wrapper.diagnostics.extend(diagnostics)


def get_diagnostics(
    server_name: str,
    *,
    file_path: str | None = None,
    severity: DiagnosticSeverity | None = None,
) -> list[LspDiagnostic]:
    wrapper = _lsp_plugins.get(server_name)
    if wrapper is None:
        return []

    result = wrapper.diagnostics
    if file_path is not None:
        result = [d for d in result if d.file_path == file_path]
    if severity is not None:
        result = [d for d in result if d.severity == severity]
    return result


def clear_diagnostics(server_name: str) -> None:
    wrapper = _lsp_plugins.get(server_name)
    if wrapper is not None:
        wrapper.diagnostics.clear()


def remove_lsp_plugin(name: str) -> bool:
    if name in _lsp_plugins:
        del _lsp_plugins[name]
        return True
    return False


def clear_lsp_plugins() -> None:
    _lsp_plugins.clear()

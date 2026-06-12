from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from src.prefetch import (
    PrefetchResult,
    get_or_start_keychain_prefetch,
    get_or_start_mdm_raw_read,
    start_project_scan,
)


# ch02 round-3: relocated from src/deferred_init.py — this flags
# snapshot is audit-manifest scaffolding; the production module now
# implements the real deferred-prefetch lane (start_deferred_prefetches).
@dataclass(frozen=True)
class DeferredInitResult:
    trusted: bool
    plugin_init: bool
    skill_init: bool
    mcp_prefetch: bool
    session_hooks: bool

    def as_lines(self) -> tuple[str, ...]:
        return (
            f'- plugin_init={self.plugin_init}',
            f'- skill_init={self.skill_init}',
            f'- mcp_prefetch={self.mcp_prefetch}',
            f'- session_hooks={self.session_hooks}',
        )


def run_deferred_init(trusted: bool) -> DeferredInitResult:
    enabled = bool(trusted)
    return DeferredInitResult(
        trusted=trusted,
        plugin_init=enabled,
        skill_init=enabled,
        mcp_prefetch=enabled,
        session_hooks=enabled,
    )


@dataclass(frozen=True)
class WorkspaceSetup:
    python_version: str
    implementation: str
    platform_name: str
    test_command: str = 'python3 -m unittest discover -s tests -v'

    def startup_steps(self) -> tuple[str, ...]:
        return (
            'start top-level prefetch side effects',
            'build workspace context',
            'load mirrored command snapshot',
            'load mirrored tool snapshot',
            'prepare parity audit hooks',
            'apply trust-gated deferred init',
        )


@dataclass(frozen=True)
class SetupReport:
    setup: WorkspaceSetup
    prefetches: tuple[PrefetchResult, ...]
    deferred_init: DeferredInitResult
    trusted: bool
    cwd: Path

    def as_markdown(self) -> str:
        lines = [
            '# Setup Report',
            '',
            f'- Python: {self.setup.python_version} ({self.setup.implementation})',
            f'- Platform: {self.setup.platform_name}',
            f'- Trusted mode: {self.trusted}',
            f'- CWD: {self.cwd}',
            '',
            'Prefetches:',
            *(f'- {prefetch.name}: {prefetch.detail}' for prefetch in self.prefetches),
            '',
            'Deferred init:',
            *self.deferred_init.as_lines(),
        ]
        return '\n'.join(lines)


def build_workspace_setup() -> WorkspaceSetup:
    return WorkspaceSetup(
        python_version='.'.join(str(part) for part in sys.version_info[:3]),
        implementation=platform.python_implementation(),
        platform_name=platform.platform(),
    )


def run_setup(cwd: Path | None = None, trusted: bool = True) -> SetupReport:
    root = cwd or Path(__file__).resolve().parent.parent
    # WI-4.1: singleton getters. ``cli.py`` may have already fired these
    # at module import time; we reuse those handles instead of re-spawning.
    prefetches = [
        get_or_start_mdm_raw_read(),
        get_or_start_keychain_prefetch(),
        start_project_scan(root),
    ]
    return SetupReport(
        setup=build_workspace_setup(),
        prefetches=tuple(prefetches),
        deferred_init=run_deferred_init(trusted=trusted),
        trusted=trusted,
        cwd=root,
    )

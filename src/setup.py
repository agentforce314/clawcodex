from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from .deferred_init import DeferredInitResult, run_deferred_init
from .prefetch import PrefetchResult, start_keychain_prefetch, start_mdm_raw_read, start_project_scan


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
    prefetches = [
        start_mdm_raw_read(),
        start_keychain_prefetch(),
        start_project_scan(root),
    ]
    return SetupReport(
        setup=build_workspace_setup(),
        prefetches=tuple(prefetches),
        deferred_init=run_deferred_init(trusted=trusted),
        trusted=trusted,
        cwd=root,
    )

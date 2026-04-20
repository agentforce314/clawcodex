from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PrefetchResult:
    name: str
    started: bool
    detail: str


def start_mdm_raw_read() -> PrefetchResult:
    return PrefetchResult('mdm_raw_read', True, 'Simulated MDM raw-read prefetch for workspace bootstrap')


def start_keychain_prefetch() -> PrefetchResult:
    return PrefetchResult('keychain_prefetch', True, 'Simulated keychain prefetch for trusted startup path')


def start_project_scan(root: Path) -> PrefetchResult:
    return PrefetchResult('project_scan', True, f'Scanned project root {root}')

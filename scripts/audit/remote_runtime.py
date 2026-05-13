"""DEPRECATED placeholder for remote/SSH/teleport modes.

The ``run_remote_mode``/``run_ssh_mode``/``run_teleport_mode`` callers
return canned strings; real CCR remote-execution lives at ``src/remote/``
and ``src/bridge/`` per ``my-docs/ch16-remote-refactoring-plan.md``.

WI-4.5 (RESERVED, post-Phase-4 cleanup) rewrites ``run_remote_mode`` to
delegate to ``RemoteSessionManager`` once the public API stabilizes.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

warnings.warn(
    'scripts.audit.remote_runtime is a placeholder; CCR remote execution lives at src/remote/ and src/bridge/.',
    DeprecationWarning,
    stacklevel=2,
)


@dataclass(frozen=True)
class RuntimeModeReport:
    mode: str
    connected: bool
    detail: str

    def as_text(self) -> str:
        return f'mode={self.mode}\nconnected={self.connected}\ndetail={self.detail}'


def run_remote_mode(target: str) -> RuntimeModeReport:
    return RuntimeModeReport('remote', True, f'Remote control placeholder prepared for {target}')


def run_ssh_mode(target: str) -> RuntimeModeReport:
    return RuntimeModeReport('ssh', True, f'SSH proxy placeholder prepared for {target}')


def run_teleport_mode(target: str) -> RuntimeModeReport:
    return RuntimeModeReport('teleport', True, f'Teleport resume/create placeholder prepared for {target}')

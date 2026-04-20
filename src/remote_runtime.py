from __future__ import annotations

from dataclasses import dataclass


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

from __future__ import annotations

from .task import PortingTask


def default_tasks() -> list[PortingTask]:
    return [
        PortingTask('root-module-parity', 'Mirror the root module surface of the archived snapshot'),
        PortingTask('directory-parity', 'Mirror top-level subsystem names as Python packages'),
        PortingTask('parity-audit', 'Continuously measure parity against the local archive'),
    ]

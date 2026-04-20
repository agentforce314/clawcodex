from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .models import Subsystem

DEFAULT_SRC_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class PortManifest:
    src_root: Path
    total_python_files: int
    top_level_modules: tuple[Subsystem, ...]

    def to_markdown(self) -> str:
        lines = [
            f'Port root: `{self.src_root}`',
            f'Total Python files: **{self.total_python_files}**',
            '',
            'Top-level Python modules:',
        ]
        for module in self.top_level_modules:
            lines.append(f'- `{module.name}` ({module.file_count} files) — {module.notes}')
        return '\n'.join(lines)


def build_port_manifest(src_root: Path | None = None) -> PortManifest:
    root = src_root or DEFAULT_SRC_ROOT
    files = [path for path in root.rglob('*.py') if path.is_file()]
    counter = Counter(
        path.relative_to(root).parts[0] if len(path.relative_to(root).parts) > 1 else path.name
        for path in files
        if path.name != '__pycache__'
    )
    notes = {
        '__init__.py': 'package export surface',
        'main.py': 'CLI entrypoint',
        'port_manifest.py': 'workspace manifest generation',
        'query_engine.py': 'port orchestration summary layer',
        'commands.py': 'command backlog metadata',
        'tools.py': 'tool backlog metadata',
        'models.py': 'shared dataclasses',
        'task.py': 'task-level planning structures',
    }
    modules = tuple(
        Subsystem(name=name, path=f'src/{name}', file_count=count, notes=notes.get(name, 'Python port support module'))
        for name, count in counter.most_common()
    )
    return PortManifest(src_root=root, total_python_files=len(files), top_level_modules=modules)

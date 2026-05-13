from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# ch01 round-2 P3: this module was relocated from src/parity_audit.py to
# scripts/audit/parity_audit.py. The audit still measures the production
# tree at <repo>/src, while its own reference_data sits alongside it.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/audit -> scripts -> repo root
ARCHIVE_ROOT = _REPO_ROOT / 'archive' / 'claude_code_ts_snapshot' / 'src'
CURRENT_ROOT = _REPO_ROOT / 'src'  # what we audit (NOT where we live)
# Reference data stays at src/reference_data/ because the production
# subsystem __init__.py files (src/bridge, src/server, ~20 more) read
# their JSON snapshots from there.
_DATA_ROOT = CURRENT_ROOT / 'reference_data'
REFERENCE_SURFACE_PATH = _DATA_ROOT / 'archive_surface_snapshot.json'
COMMAND_SNAPSHOT_PATH = _DATA_ROOT / 'commands_snapshot.json'
TOOL_SNAPSHOT_PATH = _DATA_ROOT / 'tools_snapshot.json'

ARCHIVE_ROOT_FILES = {
    # ch01 round-2 P3: legacy ``src/QueryEngine.py`` shim deleted —
    # ``QueryEngineRuntime`` had zero callers. Production ``QueryEngine``
    # lives at ``src/query/engine.py``; this row redirects to the
    # ``query`` package (same pattern as the ``query.ts`` row below).
    'QueryEngine.ts': 'query',
    # Chapter-10 refactor (Chunk B / WI-1.1): the TS ``Task.ts`` file now maps
    # to ``src/tasks_core.py`` (TaskType union, TaskStatus union, TaskStateBase,
    # ``is_terminal_task_status``, ``generate_task_id``). The legacy stub at
    # ``src/task.py`` remains for the moment as a re-export shim (PortingTask).
    'Task.ts': 'tasks_core.py',
    # ch01 refactor (P2.3): the legacy ``src/Tool.py`` stub
    # (a 15-line placeholder dataclass with zero import consumers) was
    # deleted. The canonical tool interface lives at
    # ``src/tool_system/build_tool.py``; the rest of the tool surface
    # lives in the ``src/tool_system/`` package. Mapped to the package
    # directory name (same pattern as ``'tasks.ts': 'task_registry.py'``
    # below and ``'dialogLaunchers.tsx': 'tui'`` above).
    'Tool.ts': 'tool_system',
    # ch01 round-2 P3: ``src/commands.py`` was an audit snapshot, relocated
    # to ``scripts/audit/commands.py``. The production command surface lives
    # under the ``src/command_system/`` package.
    'commands.ts': 'command_system',
    # ch01 round-2 P3: ``src/context.py`` was audit scaffolding, relocated
    # to ``scripts/audit/context.py``. Production context lives at
    # ``src/context_system/``.
    'context.ts': 'context_system',
    'cost-tracker.ts': 'cost_tracker.py',
    'costHook.ts': 'costHook.py',
    # Chapter-13 Phase-0 hygiene: ``dialogLaunchers.tsx``, ``ink.ts``, and
    # ``interactiveHelpers.tsx`` map to the ``src/tui/`` package (Textual
    # screens / app / a11y), where their behavioral equivalents live. The
    # previous flat ``src/{ink, dialogLaunchers, interactiveHelpers}.py``
    # stubs were decoys with no callers and were deleted in WI-0.1 / WI-0.4.
    # All three map to the same target (``tui``) because the audit checks
    # only immediate children of ``src/``; the per-feature breakdown lives
    # at ``my-docs/ch13-terminal-ui-gap-analysis.md`` §4 (Concrete Reference
    # Index). See gap #11 / #12 for the rationale and the refactoring plan
    # Phase 0 for the audit + deletion record.
    'dialogLaunchers.tsx': 'tui',
    'history.ts': 'history.py',
    'ink.ts': 'tui',
    'interactiveHelpers.tsx': 'tui',
    # ch01 round-2 P3: ``src/main.py`` was the audit CLI, relocated to
    # ``scripts/audit/main.py``. Production entry is ``src/cli.py``
    # (``pyproject.toml`` console-script).
    'main.tsx': 'cli.py',
    'projectOnboardingState.ts': 'projectOnboardingState.py',
    # ch01 refactor (P2.3): the legacy ``src/query.py`` stub
    # (a 13-line placeholder pair of request/response dataclasses)
    # was shadowed by ``src/query/__init__.py`` and had zero
    # functional consumers. Deleted. Canonical async-generator query
    # loop lives at ``src/query/query.py``; the rest of the loop's
    # components live in the ``src/query/`` package.
    'query.ts': 'query',
    'replLauncher.tsx': 'replLauncher.py',
    # ch01 round-2 P3: ``src/setup.py`` was audit scaffolding, relocated to
    # ``scripts/audit/setup.py``. Production initialization lives under
    # ``src/bootstrap/``.
    'setup.ts': 'bootstrap',
    # Chapter-10 refactor (Chunk B / WI-1.0): the old ``tasks.py`` flat file
    # was deleted in favor of a real ``src/tasks/`` package. The TS root file
    # ``tasks.ts`` now maps to ``src/task_registry.py`` (which holds
    # ``RuntimeTaskRegistry``, ``Task`` Protocol, and ``get_all_tasks``).
    'tasks.ts': 'task_registry.py',
    # ch01 round-2 P3: ``src/tools.py`` was a ``PortingModule`` snapshot,
    # relocated to ``scripts/audit/tools.py``. Production tools live under
    # the ``src/tool_system/`` package (same target as ``Tool.ts`` above).
    'tools.ts': 'tool_system',
}

ARCHIVE_DIR_MAPPINGS = {
    'assistant': 'assistant',
    'bootstrap': 'bootstrap',
    'bridge': 'bridge',
    'buddy': 'buddy',
    # ch01 refactor (P2.3a): the production entry point
    # (``pyproject.toml:68`` console-script ``clawcodex = "src.cli:main"``)
    # is the file ``src/cli.py``, not a ``src/cli/`` directory. The
    # earlier ``'cli': 'cli'`` row was a pre-existing baseline miss —
    # ``run_parity_audit()`` reported it under
    # ``missing_directory_targets``. Aligns with the existing
    # ``'commands': 'commands.py'`` / ``'context': 'context.py'`` /
    # ``'tools': 'tools.py'`` pattern.
    'cli': 'cli.py',
    # ch01 round-2 P3: the audit ``src/commands.py`` snapshot was relocated
    # to ``scripts/audit/commands.py``; production commands live under
    # ``src/command_system/``.
    'commands': 'command_system',
    'components': 'components',
    'constants': 'constants',
    # ch01 round-2 P3: the audit ``src/context.py`` scaffolding was relocated
    # to ``scripts/audit/context.py``; production context lives under
    # ``src/context_system/``.
    'context': 'context_system',
    'coordinator': 'coordinator',
    'entrypoints': 'entrypoints',
    'hooks': 'hooks',
    # Chapter-13 Phase-0 hygiene: the TS ``ink/`` directory's renderer
    # (custom DOM, Yoga, packed cells, blit, BSU/ESU) is correctly delegated
    # to Textual + Rich; the ``src/ink.py`` decoy was deleted in WI-0.1.
    # The user-facing surface lives at ``src/tui/`` (Textual app + widgets).
    # See ``my-docs/ch13-terminal-ui-gap-analysis.md`` gap #11 and the
    # ``Out of Scope`` block in the refactoring plan.
    'ink': 'tui',
    'keybindings': 'keybindings',
    'memdir': 'memdir',
    'migrations': 'migrations',
    'moreright': 'moreright',
    'native-ts': 'native_ts',
    'outputStyles': 'outputStyles',
    'plugins': 'plugins',
    # ch01 refactor (P2.3): TS ``query/`` directory → Python ``src/query/``
    # package (was pointing at the deleted ``src/query.py`` decoy).
    'query': 'query',
    'remote': 'remote',
    'schemas': 'schemas',
    'screens': 'screens',
    'server': 'server',
    'services': 'services',
    'skills': 'skills',
    'state': 'state',
    # Chapter-10 refactor (Chunk B / WI-1.0): TS ``tasks/`` directory now
    # maps to a real ``src/tasks/`` Python package (was a flat
    # ``src/tasks.py`` stub before the refactor).
    'tasks': 'tasks',
    # ch01 round-2 P3: production tools live under ``src/tool_system/``;
    # the legacy audit snapshot at ``src/tools.py`` was relocated to
    # ``scripts/audit/tools.py``.
    'tools': 'tool_system',
    'types': 'types',
    'upstreamproxy': 'upstreamproxy',
    'utils': 'utils',
    'vim': 'vim',
    'voice': 'voice',
}


@dataclass(frozen=True)
class ParityAuditResult:
    archive_present: bool
    root_file_coverage: tuple[int, int]
    directory_coverage: tuple[int, int]
    total_file_ratio: tuple[int, int]
    command_entry_ratio: tuple[int, int]
    tool_entry_ratio: tuple[int, int]
    missing_root_targets: tuple[str, ...]
    missing_directory_targets: tuple[str, ...]

    def to_markdown(self) -> str:
        lines = ['# Parity Audit']
        if not self.archive_present:
            lines.append('Local archive unavailable; parity audit cannot compare against the original snapshot.')
            return '\n'.join(lines)

        lines.extend([
            '',
            f'Root file coverage: **{self.root_file_coverage[0]}/{self.root_file_coverage[1]}**',
            f'Directory coverage: **{self.directory_coverage[0]}/{self.directory_coverage[1]}**',
            f'Total Python files vs archived TS-like files: **{self.total_file_ratio[0]}/{self.total_file_ratio[1]}**',
            f'Command entry coverage: **{self.command_entry_ratio[0]}/{self.command_entry_ratio[1]}**',
            f'Tool entry coverage: **{self.tool_entry_ratio[0]}/{self.tool_entry_ratio[1]}**',
            '',
            'Missing root targets:',
        ])
        if self.missing_root_targets:
            lines.extend(f'- {item}' for item in self.missing_root_targets)
        else:
            lines.append('- none')

        lines.extend(['', 'Missing directory targets:'])
        if self.missing_directory_targets:
            lines.extend(f'- {item}' for item in self.missing_directory_targets)
        else:
            lines.append('- none')
        return '\n'.join(lines)


def _reference_surface() -> dict[str, object]:
    return json.loads(REFERENCE_SURFACE_PATH.read_text())


def _snapshot_count(path: Path) -> int:
    return len(json.loads(path.read_text()))


def run_parity_audit() -> ParityAuditResult:
    current_entries = {path.name for path in CURRENT_ROOT.iterdir()}
    root_hits = [target for target in ARCHIVE_ROOT_FILES.values() if target in current_entries]
    dir_hits = [target for target in ARCHIVE_DIR_MAPPINGS.values() if target in current_entries]
    missing_roots = tuple(target for target in ARCHIVE_ROOT_FILES.values() if target not in current_entries)
    missing_dirs = tuple(target for target in ARCHIVE_DIR_MAPPINGS.values() if target not in current_entries)
    current_python_files = sum(1 for path in CURRENT_ROOT.rglob('*.py') if path.is_file())
    reference = _reference_surface()
    return ParityAuditResult(
        archive_present=ARCHIVE_ROOT.exists(),
        root_file_coverage=(len(root_hits), len(ARCHIVE_ROOT_FILES)),
        directory_coverage=(len(dir_hits), len(ARCHIVE_DIR_MAPPINGS)),
        total_file_ratio=(current_python_files, int(reference['total_ts_like_files'])),
        command_entry_ratio=(_snapshot_count(COMMAND_SNAPSHOT_PATH), int(reference['command_entry_count'])),
        tool_entry_ratio=(_snapshot_count(TOOL_SNAPSHOT_PATH), int(reference['tool_entry_count'])),
        missing_root_targets=missing_roots,
        missing_directory_targets=missing_dirs,
    )

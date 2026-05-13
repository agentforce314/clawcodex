"""Architecture-observability inspector.

Chapter 18 (Epilogue) reflects on the codebase as six core abstractions and
claims that "behavioral complexity concentrates in a small number of
high-density files." This module produces a Python-port equivalent of that
reading aid: for each of the six abstractions named in the book's closing,
report the package's footprint (files, LOC, lines/file) and surface the
high-density files (>= ``HIGH_DENSITY_THRESHOLD`` LOC) inside it.

Book references:

- ``claude-code-from-source/book/ch18-epilogue.md`` §Closing (line 127) —
  enumerates the six abstractions in the order this module preserves:
  generator loop, tools, memory, hooks, rendering engine, MCP.
- Same file §The Cost of Complexity (line 75) — establishes the
  "high-density files" heuristic and cites 1.7k / 4.9k / 5k LOC examples
  from the TS reference. ``HIGH_DENSITY_THRESHOLD = 500`` is the
  order-of-magnitude floor that catches meaningful clusters.

Wired as ``python -m scripts.audit.main architecture-stats``. Importable as
``scripts.audit.architecture_stats.build_architecture_stats``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Sentinel above which a file is called out as a "high-density" file.
# See module docstring for the rationale.
HIGH_DENSITY_THRESHOLD = 500

# Six core abstractions, in the order the book enumerates them in §Closing
# (line 127). The order is load-bearing for the test that pins the map; do
# not reshuffle without updating ``test_six_abstractions_present``.
ABSTRACTION_MAP: tuple[tuple[str, str], ...] = (
    ("Generator loop", "src/query"),
    ("Tools", "src/tool_system"),
    ("Memory", "src/memdir"),
    ("Hooks", "src/hooks"),
    ("Rendering engine", "src/tui"),
    ("MCP", "src/services/mcp"),
)

# At most this many high-density files are reported per abstraction. Keeping
# the per-section list short keeps the report readable; the threshold is the
# real signal.
MAX_HIGH_DENSITY_PER_ABSTRACTION = 3


def _repo_root() -> Path:
    # scripts/audit/architecture_stats.py -> scripts/audit -> scripts -> repo
    return Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class HighDensityFile:
    """A single >= HIGH_DENSITY_THRESHOLD LOC file inside an abstraction."""

    relative_path: str
    line_count: int


@dataclass(frozen=True)
class AbstractionStats:
    """Per-abstraction footprint."""

    name: str
    package: str
    file_count: int
    line_count: int
    high_density_files: tuple[HighDensityFile, ...]

    @property
    def lines_per_file(self) -> float:
        if self.file_count == 0:
            return 0.0
        return self.line_count / self.file_count


@dataclass(frozen=True)
class ArchitectureStats:
    """The full six-abstraction report."""

    abstractions: tuple[AbstractionStats, ...]
    total_files: int
    total_lines: int

    def as_markdown(self) -> str:
        return _render_markdown(self)


def _walk_python_files(package_dir: Path) -> list[Path]:
    if not package_dir.exists():
        return []
    return sorted(p for p in package_dir.rglob("*.py") if p.is_file())


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _build_abstraction(name: str, package_rel: str, root: Path) -> AbstractionStats:
    package_dir = root / package_rel
    files = _walk_python_files(package_dir)
    sized: list[tuple[Path, int]] = [(p, _count_lines(p)) for p in files]
    total_lines = sum(lc for _, lc in sized)

    high = sorted(
        (
            HighDensityFile(
                relative_path=str(p.relative_to(root)),
                line_count=lc,
            )
            for p, lc in sized
            if lc >= HIGH_DENSITY_THRESHOLD
        ),
        key=lambda f: f.line_count,
        reverse=True,
    )

    return AbstractionStats(
        name=name,
        package=package_rel,
        file_count=len(files),
        line_count=total_lines,
        high_density_files=tuple(high[:MAX_HIGH_DENSITY_PER_ABSTRACTION]),
    )


def build_architecture_stats(root: Path | None = None) -> ArchitectureStats:
    """Build the six-abstraction report.

    ``root`` defaults to the repo root; passing an explicit value lets tests
    point at a fixture tree.
    """

    repo_root = root if root is not None else _repo_root()
    abstractions = tuple(
        _build_abstraction(name, package_rel, repo_root)
        for name, package_rel in ABSTRACTION_MAP
    )
    return ArchitectureStats(
        abstractions=abstractions,
        total_files=sum(a.file_count for a in abstractions),
        total_lines=sum(a.line_count for a in abstractions),
    )


def _render_markdown(stats: ArchitectureStats) -> str:
    lines: list[str] = []
    lines.append("# Architecture Stats")
    lines.append("")
    lines.append(
        "The book's epilogue identifies six core abstractions "
        "(ch18 §Closing). This is their Python port footprint."
    )
    lines.append("")
    lines.append("| # | Abstraction | Package | Files | Lines | Lines/file |")
    lines.append("|---|---|---|---:|---:|---:|")
    for idx, abstraction in enumerate(stats.abstractions, start=1):
        lines.append(
            f"| {idx} | {abstraction.name} | `{abstraction.package}` | "
            f"{abstraction.file_count} | {abstraction.line_count} | "
            f"{abstraction.lines_per_file:.0f} |"
        )
    lines.append("")
    lines.append(
        f"**Totals:** {stats.total_files} files, {stats.total_lines} lines "
        f"across the six abstractions."
    )
    lines.append("")
    lines.append(f"## High-density files (>= {HIGH_DENSITY_THRESHOLD} LOC)")
    lines.append("")
    lines.append(
        "Per the chapter's claim that complexity concentrates in a small "
        "number of files (ch18 §The Cost of Complexity)."
    )
    lines.append("")
    any_dense = False
    for abstraction in stats.abstractions:
        if not abstraction.high_density_files:
            continue
        any_dense = True
        lines.append(f"### {abstraction.name}")
        for hd in abstraction.high_density_files:
            lines.append(f"- `{hd.relative_path}` — {hd.line_count} lines")
        lines.append("")
    if not any_dense:
        lines.append(
            "_No files meet the high-density threshold; "
            "all abstractions are evenly distributed._"
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

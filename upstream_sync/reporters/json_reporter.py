# upstream_sync/reporters/json_reporter.py
"""JSON report generator.

Emits machine-readable reports suitable for CI artifacts, agent consumption,
and programmatic diffing.
"""

from __future__ import annotations

from pathlib import Path

from upstream_sync.core.change_analyzer import ChangeReport


class JSONReporter:
    """Writes ``ChangeReport`` to disk as formatted JSON."""

    def emit(self, report: ChangeReport, path: Path) -> None:
        """Serialize *report* to JSON and write it to *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.to_json(indent=2), encoding="utf-8")

# upstream_sync/reporters/markdown_reporter.py
"""Markdown report generator.

Produces human-readable reports with headings, tables, and action-item
checklists that can be pasted into GitHub issues or PR descriptions.
"""

from __future__ import annotations

from pathlib import Path

from upstream_sync.core.change_analyzer import ChangeReport


class MarkdownReporter:
    """Writes ``ChangeReport`` to disk as Markdown."""

    def emit(self, report: ChangeReport, path: Path) -> None:
        """Render *report* as Markdown and write it to *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = [
            f"# Upstream Sync Report: {report.upstream_version}",
            "",
            f"- **Previous Version**: {report.previous_version}",
            f"- **Overall Impact**: {report.overall_impact}",
            f"- **Files Changed**: {report.statistics.get('files_changed_upstream', 'N/A')}",
            "",
            "## Module Impacts",
            "",
        ]

        for imp in report.module_impacts:
            lines.extend([
                f"### {imp.module_name}",
                "",
                f"- **Layer**: {imp.layer_name}",
                f"- **Conflict Probability**: {imp.conflict_probability}",
                f"- **Recommended Strategy**: {imp.recommended_strategy}",
                f"- **Estimated Effort**: {imp.estimated_effort_minutes} minutes",
                f"- **Files Changed**: {len(imp.files_changed)}",
                f"- **Patches Affected**: {', '.join(imp.patches_affected) or 'None'}",
                "",
            ])

        if report.action_items:
            lines.extend([
                "## Action Items",
                "",
            ])
            for item in report.action_items:
                lines.append(
                    f"- [{item['action'].upper()}] "
                    f"{item['module']}: {item['reason']}"
                )
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")

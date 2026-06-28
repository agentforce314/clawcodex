"""Tests for the ch18 architecture-stats inspector.

Pins the six-abstraction map declared in ``ch18-epilogue-plan.md`` so a
rename of any mapped package (or a quiet reshuffle of the order) is caught
in CI rather than at "why is the inspector empty?" time. See also
``my-docs/ch18-epilogue-gap-analysis.md``.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.audit.architecture_stats import (
    ABSTRACTION_MAP,
    HIGH_DENSITY_THRESHOLD,
    MAX_HIGH_DENSITY_PER_ABSTRACTION,
    build_architecture_stats,
)


# Pinned names + packages from the book's §Closing list (line 127). The
# fifth slot — the book's in-process "Rendering engine" (``src/tui``) — was
# repointed to the Python ``src/server`` agent-server after the UI moved to the
# TypeScript Ink client (``ui-tui/``). See ABSTRACTION_MAP for the rationale.
EXPECTED_ABSTRACTIONS: tuple[tuple[str, str], ...] = (
    ("Generator loop", "src/query"),
    ("Tools", "src/tool_system"),
    ("Memory", "src/memdir"),
    ("Hooks", "src/hooks"),
    ("Agent server", "src/server"),
    ("MCP", "src/services/mcp"),
)


def _repo_root() -> Path:
    # tests/test_architecture_stats.py -> tests -> repo
    return Path(__file__).resolve().parent.parent


class ArchitectureStatsTests(unittest.TestCase):
    def test_six_abstractions_present(self) -> None:
        """The map must have exactly six entries, in the book's order."""
        self.assertEqual(ABSTRACTION_MAP, EXPECTED_ABSTRACTIONS)

        stats = build_architecture_stats()
        self.assertEqual(len(stats.abstractions), 6)
        for got, (expected_name, expected_pkg) in zip(
            stats.abstractions, EXPECTED_ABSTRACTIONS
        ):
            self.assertEqual(got.name, expected_name)
            self.assertEqual(got.package, expected_pkg)

    def test_each_package_exists_on_disk(self) -> None:
        """A package rename should fail this test, not silently zero out."""
        root = _repo_root()
        for _, package_rel in EXPECTED_ABSTRACTIONS:
            package_dir = root / package_rel
            self.assertTrue(
                package_dir.is_dir(),
                f"abstraction package missing on disk: {package_dir}",
            )
            py_files = list(package_dir.rglob("*.py"))
            self.assertGreaterEqual(
                len(py_files),
                1,
                f"abstraction {package_rel} has no .py files — likely a rename",
            )

    def test_each_abstraction_has_nonzero_size(self) -> None:
        """Structural check, deliberately not a snapshot."""
        stats = build_architecture_stats()
        for abstraction in stats.abstractions:
            self.assertGreaterEqual(abstraction.file_count, 1)
            # 100 LOC floor — small enough not to break on cleanup, large
            # enough to detect "wired to an empty package".
            self.assertGreaterEqual(abstraction.line_count, 100)
            self.assertGreater(abstraction.lines_per_file, 0)

    def test_markdown_render_includes_all_six(self) -> None:
        stats = build_architecture_stats()
        rendered = stats.as_markdown()
        self.assertIn("# Architecture Stats", rendered)
        for name, package in EXPECTED_ABSTRACTIONS:
            self.assertIn(name, rendered)
            self.assertIn(package, rendered)
        self.assertIn("Totals:", rendered)
        self.assertIn(f"{HIGH_DENSITY_THRESHOLD}", rendered)

    def test_high_density_files_capped_per_abstraction(self) -> None:
        stats = build_architecture_stats()
        for abstraction in stats.abstractions:
            self.assertLessEqual(
                len(abstraction.high_density_files),
                MAX_HIGH_DENSITY_PER_ABSTRACTION,
                f"{abstraction.name} reported too many high-density files",
            )
            for hd in abstraction.high_density_files:
                self.assertGreaterEqual(hd.line_count, HIGH_DENSITY_THRESHOLD)
                self.assertTrue(hd.relative_path.startswith(abstraction.package))

    def test_high_density_threshold_constant(self) -> None:
        """A drive-by tweak to the threshold should require code review.

        The 500-LOC floor is justified in
        ``scripts/audit/architecture_stats.py`` and
        ``my-docs/ch18-epilogue-plan.md``; pin it.
        """
        self.assertEqual(HIGH_DENSITY_THRESHOLD, 500)
        self.assertEqual(MAX_HIGH_DENSITY_PER_ABSTRACTION, 3)

    def test_totals_match_per_abstraction_sums(self) -> None:
        stats = build_architecture_stats()
        self.assertEqual(
            stats.total_files,
            sum(a.file_count for a in stats.abstractions),
        )
        self.assertEqual(
            stats.total_lines,
            sum(a.line_count for a in stats.abstractions),
        )


if __name__ == "__main__":
    unittest.main()

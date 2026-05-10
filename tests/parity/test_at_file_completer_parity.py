"""Parity test: ``at_file_completer`` produces identical suggestions
across the legacy import path and the new shared utility path.

Phase-2 §19 of the ch13 refactoring plan introduced a behavioral
parity suite that runs on every PR so silent regressions in the
legacy REPL are caught when shared utilities change. This is the
first member: ``at_file_completer`` was moved from ``src/repl/`` to
``src/utils/`` in Phase 3 WI-3.1; both import paths must continue to
yield byte-identical suggestion lists for the same query.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fixture_workspace(tmp_path: Path) -> Path:
    """Build a small fixed workspace for the completer to scan."""

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("# alpha\n")
    (tmp_path / "src" / "beta.py").write_text("# beta\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_alpha.py").write_text("# test\n")
    (tmp_path / "README.md").write_text("# fixture\n")
    return tmp_path


def _completions_via(import_path: str, workspace: Path, query: str) -> list[str]:
    """Drive the completer at ``import_path`` and return suggestion text.

    Both legacy and new paths expose ``AtFileCompleter`` with the same
    constructor signature; we drive it via prompt_toolkit's ``Document``
    interface (which is what the legacy REPL uses).
    """

    if import_path == "legacy":
        from src.repl.at_file_completer import AtFileCompleter
    else:
        from src.utils.at_file_completer import AtFileCompleter
    from prompt_toolkit.document import Document

    completer = AtFileCompleter(cwd=workspace)
    doc = Document(text=query, cursor_position=len(query))
    return [c.text for c in completer.get_completions(doc, None)]


def test_at_file_completer_legacy_and_new_produce_identical_lists(
    fixture_workspace: Path,
) -> None:
    """``@README`` produces the same suggestions across both import paths."""

    legacy = _completions_via("legacy", fixture_workspace, "@README")
    new = _completions_via("new", fixture_workspace, "@README")
    assert legacy == new
    # And the README is among them so the test isn't vacuously equal-empty.
    assert any("README.md" in suggestion for suggestion in legacy), (
        f"expected README.md in suggestions, got {legacy!r}"
    )


def test_at_file_completer_query_with_partial_path_parity(
    fixture_workspace: Path,
) -> None:
    """``@src/al`` matches the alpha file across both paths identically."""

    legacy = _completions_via("legacy", fixture_workspace, "@src/al")
    new = _completions_via("new", fixture_workspace, "@src/al")
    assert legacy == new


def test_at_file_completer_no_match_parity(fixture_workspace: Path) -> None:
    """No-match query produces the same (empty) result on both paths."""

    legacy = _completions_via(
        "legacy", fixture_workspace, "@no-such-prefix-zzzz"
    )
    new = _completions_via("new", fixture_workspace, "@no-such-prefix-zzzz")
    assert legacy == new


def test_legacy_path_imports_resolve() -> None:
    """The legacy import surface stays importable for one release cycle.

    Includes the underscored helpers tests reach into. Catches a future
    rename at the new location that doesn't propagate to the shim.
    """

    from src.repl.at_file_completer import (  # noqa: F401
        AtFileCompleter,
        _filter_candidates,
        _is_path_like_token,
        _path_completions,
        _subsequence_score,
    )


def test_legacy_path_resolves_to_same_class_object() -> None:
    """Shim re-exports the class — not a copy. Identity check ensures
    isinstance() works across both paths."""

    from src.repl.at_file_completer import AtFileCompleter as Legacy
    from src.utils.at_file_completer import AtFileCompleter as New

    assert Legacy is New

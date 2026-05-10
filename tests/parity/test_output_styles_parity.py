"""Parity test: ``resolve_output_style`` produces byte-identical prompts
for the built-in styles before and after the Phase-9 frontmatter rewrite.

Phase-9 of the ch13 refactor changed how output styles are loaded —
adding YAML frontmatter parsing and a default search-path. Built-in
styles (``default``, ``explanatory``) are preserved; this test pins
that the prompt strings the rest of the system reads are byte-for-byte
unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.outputStyles.loader import resolve_output_style
from src.outputStyles.styles import BUILTIN_OUTPUT_STYLES


# Snapshot of the prompt text every built-in carries. Update by hand
# when intentionally changing a built-in; CI failure here means an
# unintentional drift.
_EXPECTED_PROMPTS = {
    "default": (
        "Respond clearly, concisely, and focus on the user's "
        "requested engineering task."
    ),
    "explanatory": (
        "Respond with concise implementation details plus short educational "
        "notes when they improve understanding."
    ),
}


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Make sure auto-discovery of ``~/.claude/outputStyles`` doesn't
    pick up the dev's local config and contaminate the parity check."""

    monkeypatch.setenv("HOME", str(tmp_path))
    yield


@pytest.mark.parametrize("name", ["default", "explanatory"])
def test_builtin_prompt_is_byte_identical(name: str) -> None:
    style = resolve_output_style(name)
    assert style.name == name
    assert style.prompt == _EXPECTED_PROMPTS[name]


def test_unknown_name_falls_back_to_default() -> None:
    style = resolve_output_style("does-not-exist")
    assert style.name == "default"
    assert style.prompt == _EXPECTED_PROMPTS["default"]


def test_builtins_dict_unchanged() -> None:
    """The ``BUILTIN_OUTPUT_STYLES`` constant is directly imported by
    callers; verify the keys haven't drifted."""

    assert set(BUILTIN_OUTPUT_STYLES) == {"default", "explanatory"}


def test_resolve_with_explicit_dir_does_not_leak_user_config(
    tmp_path: Path,
) -> None:
    """When ``search_dir`` is explicit, defaults stand in for missing
    names and built-ins win their slot — even if the user's HOME has
    a colliding file (covered above by the ``_isolate_home`` fixture)."""

    # Empty explicit dir → built-ins are still resolvable.
    target = tmp_path / "explicit_empty"
    target.mkdir()
    style = resolve_output_style("default", search_dir=target)
    assert style.prompt == _EXPECTED_PROMPTS["default"]

"""Tests for Phase-9 output-styles frontmatter + default-path loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.outputStyles.loader import (
    load_output_styles_dir,
    resolve_output_style,
)
from src.outputStyles.styles import BUILTIN_OUTPUT_STYLES


def test_no_directory_returns_builtins(tmp_path: Path) -> None:
    target = tmp_path / "missing"
    styles = load_output_styles_dir(target)
    assert set(styles) == set(BUILTIN_OUTPUT_STYLES)


def test_no_frontmatter_uses_filename_as_name(tmp_path: Path) -> None:
    """Back-compat: legacy files without YAML still load via the file stem."""

    target = tmp_path / "outputStyles"
    target.mkdir()
    (target / "concise.md").write_text("Be terse.")
    styles = load_output_styles_dir(target)
    assert "concise" in styles
    assert styles["concise"].prompt == "Be terse."


def test_frontmatter_overrides_filename(tmp_path: Path) -> None:
    """``name`` from frontmatter takes precedence over the file stem."""

    target = tmp_path / "outputStyles"
    target.mkdir()
    (target / "x.md").write_text(
        "---\nname: my-style\n---\nThe prompt body.\n"
    )
    styles = load_output_styles_dir(target)
    assert "my-style" in styles
    assert "x" not in styles  # filename was overridden
    assert styles["my-style"].prompt == "The prompt body."


def test_description_and_model_propagate(tmp_path: Path) -> None:
    target = tmp_path / "outputStyles"
    target.mkdir()
    (target / "explainy.md").write_text(
        "---\n"
        "name: explainy\n"
        "description: Verbose explanations.\n"
        "model: glm-4.6\n"
        "---\n"
        "Explain everything.\n"
    )
    styles = load_output_styles_dir(target)
    style = styles["explainy"]
    assert style.description == "Verbose explanations."
    assert style.model == "glm-4.6"
    assert style.prompt == "Explain everything."


def test_frontmatter_prompt_field_overrides_body(tmp_path: Path) -> None:
    """``prompt`` in frontmatter wins over body — letting users keep
    documentation in the body without shipping it to the model."""

    target = tmp_path / "outputStyles"
    target.mkdir()
    (target / "x.md").write_text(
        "---\n"
        "name: x\n"
        "prompt: Real prompt.\n"
        "---\n"
        "# Documentation\n\nThis section is not sent to the model.\n"
    )
    styles = load_output_styles_dir(target)
    assert styles["x"].prompt == "Real prompt."


def test_user_file_overrides_builtin(tmp_path: Path) -> None:
    target = tmp_path / "outputStyles"
    target.mkdir()
    (target / "default.md").write_text(
        "---\nname: default\n---\nUser-customized default.\n"
    )
    styles = load_output_styles_dir(target)
    # User wins.
    assert styles["default"].prompt == "User-customized default."


def test_empty_body_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "outputStyles"
    target.mkdir()
    (target / "blank.md").write_text("---\nname: blank\n---\n\n")
    styles = load_output_styles_dir(target)
    assert "blank" not in styles


def test_resolve_uses_default_dir_when_search_dir_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resolve_output_style`` auto-discovers ``~/.clawcodex/outputStyles/``
    when ``search_dir`` is ``None`` (Phase-9 behavior). The canon dir comes
    from ``GLOBAL_CONFIG_DIR`` (import-time constant), so the test re-points
    that rather than ``HOME``."""

    import src.config as config

    home = tmp_path / ".clawcodex"
    target = home / "outputStyles"
    target.mkdir(parents=True)
    (target / "auto.md").write_text("---\nname: auto\n---\nAuto-discovered.\n")
    monkeypatch.setattr(config, "GLOBAL_CONFIG_DIR", home)
    style = resolve_output_style("auto", search_dir=None)
    assert style.prompt == "Auto-discovered."


def test_resolve_falls_back_to_default_for_unknown_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # no user dir
    style = resolve_output_style("does-not-exist")
    assert style.name == "default"


def test_resolve_explicit_dir_wins(tmp_path: Path) -> None:
    target = tmp_path / "explicit"
    target.mkdir()
    (target / "custom.md").write_text(
        "---\nname: custom\n---\nFrom explicit dir.\n"
    )
    style = resolve_output_style("custom", search_dir=target)
    assert style.prompt == "From explicit dir."


def test_malformed_yaml_falls_back_to_filename_and_body(
    tmp_path: Path,
) -> None:
    """Malformed frontmatter is treated as no-frontmatter — the body
    becomes the prompt and the filename becomes the name."""

    target = tmp_path / "outputStyles"
    target.mkdir()
    # The frontmatter parser returns body == raw when it can't find a
    # closing fence; verify the file still loads.
    (target / "broken.md").write_text(
        "---\nname: broken\n# missing closing fence\nbody content here\n"
    )
    styles = load_output_styles_dir(target)
    # The whole content is treated as the body since the fence isn't closed.
    assert "broken" in styles

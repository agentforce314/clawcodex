"""Group C — Frontmatter parser & field validators (covers DEV-3).

DEV-3 swapped the homegrown frontmatter parser for ``yaml.safe_load`` so
nested structures (`hooks:`, `shell:`) round-trip, and added per-field
validators in ``loader.parse_skill_frontmatter_fields`` that drop bad
values rather than raising. These tests pin the parser/validator
contracts so a regression that silently crashes-on-bad-input or starts
swallowing valid input fails loudly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import pytest

from src.skills.bundled_skills import clear_bundled_skills
from src.skills.frontmatter import parse_frontmatter
from src.skills.loader import (
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
    load_skills_from_skills_dir,
    parse_skill_frontmatter_fields,
)


@pytest.fixture(autouse=True)
def _clean_skill_state() -> Iterator[None]:
    clear_skill_caches()
    clear_dynamic_skills()
    clear_skill_registry()
    clear_bundled_skills()
    yield
    clear_skill_caches()
    clear_dynamic_skills()
    clear_skill_registry()
    clear_bundled_skills()


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# ======================================================================
# Frontmatter parser (the YAML layer that produces the raw dict)
# ======================================================================


class TestFrontmatterParser:
    def test_nested_hooks_round_trip(self) -> None:
        md = (
            "---\n"
            "description: H\n"
            "hooks:\n"
            "  PostToolUse:\n"
            "    - matcher: Write\n"
            "      hooks:\n"
            "        - type: command\n"
            "          command: ./scripts/format.sh\n"
            "---\n"
            "body\n"
        )
        result = parse_frontmatter(md)
        hooks = result.frontmatter["hooks"]
        # Round-trips into a real nested dict, not a stringified blob.
        assert isinstance(hooks, dict)
        assert "PostToolUse" in hooks
        post = hooks["PostToolUse"]
        assert isinstance(post, list)
        assert post[0]["matcher"] == "Write"
        assert post[0]["hooks"][0]["type"] == "command"
        assert post[0]["hooks"][0]["command"] == "./scripts/format.sh"

    def test_multiline_pipe_description_joins(self) -> None:
        md = (
            "---\n"
            "description: |\n"
            "  Line one\n"
            "  Line two\n"
            "---\n"
            "body\n"
        )
        result = parse_frontmatter(md)
        # YAML `|` preserves newlines as a single string.
        desc = result.frontmatter["description"]
        assert isinstance(desc, str)
        assert "Line one" in desc
        assert "Line two" in desc

    def test_allowed_tools_preserves_paren_arg_pattern(self) -> None:
        # The TS `allowed-tools` syntax allows `Tool(arg-pattern)`. The
        # YAML parser must keep these as single list entries, not split
        # them on the `:` inside the parens.
        md = (
            "---\n"
            'allowed-tools: ["Bash(git diff:*)", "Read"]\n'
            "---\n"
            "body\n"
        )
        result = parse_frontmatter(md)
        tools = result.frontmatter["allowed-tools"]
        assert tools == ["Bash(git diff:*)", "Read"], (
            f"paren-arg pattern was split or mangled: {tools!r}"
        )

    def test_malformed_yaml_does_not_raise(self, caplog: pytest.LogCaptureFixture) -> None:
        # A bad YAML body must not crash the loader — it should return
        # an empty frontmatter and the body intact. This is the
        # "never block-load other skills" contract.
        md = (
            "---\n"
            "description: ok\n"
            "  bad: indent: here\n"  # malformed
            "---\n"
            "body content\n"
        )
        with caplog.at_level(logging.DEBUG, logger="src.skills.frontmatter"):
            result = parse_frontmatter(md)
        # Must not raise; frontmatter is empty, body is preserved.
        assert result.frontmatter == {}
        assert "body content" in result.body

    def test_no_frontmatter_returns_empty_dict_and_full_body(self) -> None:
        md = "no fence here\nstill no fence\n"
        result = parse_frontmatter(md)
        assert result.frontmatter == {}
        assert result.body == md


# ======================================================================
# parse_skill_frontmatter_fields — coercions / validators
# ======================================================================


class TestFieldValidators:
    def test_model_inherit_returns_none(self) -> None:
        result = parse_skill_frontmatter_fields(
            {"model": "inherit"}, "", "s"
        )
        assert result["model"] is None, (
            "`model: inherit` must yield None so callers fall back to "
            "the active session model (TS parity)."
        )

    def test_model_unset_returns_none(self) -> None:
        assert parse_skill_frontmatter_fields({}, "", "s")["model"] is None

    def test_model_known_value_kept(self) -> None:
        # A canonical-looking model name is preserved as-is.
        result = parse_skill_frontmatter_fields(
            {"model": "claude-sonnet-4-5"}, "", "s"
        )
        assert result["model"] == "claude-sonnet-4-5"

    def test_effort_low_kept(self) -> None:
        result = parse_skill_frontmatter_fields({"effort": "low"}, "", "s")
        assert result["effort"] == "low"

    def test_effort_medium_high_max_kept(self) -> None:
        for level in ("medium", "high", "max"):
            result = parse_skill_frontmatter_fields({"effort": level}, "", "s")
            assert result["effort"] == level

    def test_effort_invalid_drops_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.skills.loader"):
            result = parse_skill_frontmatter_fields(
                {"effort": "garbage"}, "", "s"
            )
        assert result["effort"] is None, (
            "invalid effort levels must be dropped (None), not preserved"
        )
        # Confirm we logged the rejection — the spec asks us to capture
        # the warning explicitly with caplog.
        assert any(
            "effort" in rec.getMessage().lower() for rec in caplog.records
        ), "expected a warning log for invalid effort"

    def test_effort_integer_kept_as_string(self) -> None:
        # TS' parseEffortValue accepts numeric strings too.
        result = parse_skill_frontmatter_fields({"effort": 3}, "", "s")
        assert result["effort"] == "3"

    def test_allowed_tools_preserves_paren_arg_pattern(self) -> None:
        result = parse_skill_frontmatter_fields(
            {"allowed-tools": ["Bash(git diff:*)"]}, "", "s"
        )
        assert result["allowed_tools"] == ["Bash(git diff:*)"], (
            "paren-arg pattern must be preserved as one entry; "
            "splitting on `:` would break Bash command rules"
        )

    def test_allowed_tools_string_form_split_on_comma(self) -> None:
        result = parse_skill_frontmatter_fields(
            {"allowed-tools": "Bash, Read"}, "", "s"
        )
        # String form is split on commas.
        assert result["allowed_tools"] == ["Bash", "Read"]

    def test_hooks_valid_event_round_trips_to_dict(self) -> None:
        hooks_in = {
            "PostToolUse": [
                {"matcher": "Write", "hooks": [{"type": "command", "command": "x"}]}
            ]
        }
        result = parse_skill_frontmatter_fields({"hooks": hooks_in}, "", "s")
        assert result["hooks"] == hooks_in

    def test_hooks_unknown_event_drops_silently(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="src.skills.loader"):
            result = parse_skill_frontmatter_fields(
                {"hooks": {"BogusEvent": [{"hooks": [{"type": "command"}]}]}},
                "",
                "s",
            )
        assert result["hooks"] is None

    def test_hooks_malformed_inner_drops(self) -> None:
        # Inner hooks list missing `type` must drop the whole hooks dict.
        result = parse_skill_frontmatter_fields(
            {
                "hooks": {
                    "PostToolUse": [{"hooks": [{"command": "no-type-field"}]}]
                }
            },
            "",
            "s",
        )
        assert result["hooks"] is None

    def test_shell_bash_kept(self) -> None:
        result = parse_skill_frontmatter_fields({"shell": "bash"}, "", "s")
        assert result["shell"] == "bash"

    def test_shell_powershell_kept(self) -> None:
        result = parse_skill_frontmatter_fields({"shell": "powershell"}, "", "s")
        assert result["shell"] == "powershell"

    def test_shell_invalid_drops_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.skills.loader"):
            result = parse_skill_frontmatter_fields(
                {"shell": "fish"}, "", "s"
            )
        assert result["shell"] is None


# ======================================================================
# Round-trip via load_skills_from_skills_dir — confirms the new fields
# (hooks, shell) ride all the way onto the Skill dataclass when a real
# SKILL.md is loaded from disk.
# ======================================================================


class TestSkillRoundTrip:
    def test_hooks_field_lands_on_skill(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        body = (
            "---\n"
            "description: H\n"
            "hooks:\n"
            "  PostToolUse:\n"
            "    - matcher: Write\n"
            "      hooks:\n"
            "        - type: command\n"
            "          command: ./fmt.sh\n"
            "---\n"
            "body\n"
        )
        _write_skill(skills_dir / "withhooks" / "SKILL.md", body)

        skills = load_skills_from_skills_dir(str(skills_dir), "projectSettings")
        assert len(skills) == 1
        skill = skills[0]
        assert skill.hooks is not None
        assert "PostToolUse" in skill.hooks
        assert skill.hooks["PostToolUse"][0]["matcher"] == "Write"

    def test_shell_field_lands_on_skill(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        _write_skill(
            skills_dir / "shsk" / "SKILL.md",
            "---\ndescription: S\nshell: bash\n---\nbody\n",
        )
        skills = load_skills_from_skills_dir(str(skills_dir), "projectSettings")
        assert skills[0].shell == "bash"

    def test_invalid_yaml_skill_is_skipped_quietly_others_still_load(
        self, tmp_path: Path
    ) -> None:
        skills_dir = tmp_path / "skills"
        # Bad skill.
        _write_skill(
            skills_dir / "broken" / "SKILL.md",
            "---\ndescription: ok\n  bad: yaml\n---\nbody",
        )
        # Good skill alongside it.
        _write_skill(
            skills_dir / "good" / "SKILL.md",
            "---\ndescription: ok\n---\nbody",
        )
        skills = load_skills_from_skills_dir(str(skills_dir), "projectSettings")
        names = {s.name for s in skills}
        # The "good" skill must still load. The broken one falls back
        # to an empty frontmatter dict but still loads; what matters
        # is that one bad skill doesn't kill the whole batch.
        assert "good" in names

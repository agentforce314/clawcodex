"""Tests for the YAML frontmatter parser + field validators.

Covers DEV-3 acceptance criteria:
1. Nested ``hooks:`` parses to a dict on Skill.
2. Multi-line ``description: |`` joins.
3. ``model: inherit`` -> None; valid → kept; bogus → warning logged.
4. ``effort:`` accepts ``low/medium/high/max`` and integers; rejects garbage.
5. ``allowed-tools: [Bash(git diff:*), Read]`` preserves arg-pattern intact.
6. Invalid hook shape → ``Skill.hooks is None`` (no exception).
7. Existing tests in test_skills_loader_ws8.py / test_skills_system.py
   keep passing (covered separately).
"""

from __future__ import annotations

import logging
import unittest
from pathlib import Path

from src.skills.frontmatter import parse_frontmatter
from src.skills.loader import (
    EFFORT_LEVELS,
    FRONTMATTER_SHELLS,
    _coerce_allowed_tools,
    _coerce_description,
    _coerce_effort,
    _coerce_hooks,
    _coerce_model,
    _coerce_shell,
    _extract_description_from_markdown,
    load_skills_from_skills_dir,
    parse_skill_frontmatter_fields,
)


# ----------------------------------------------------------------------
# Frontmatter parser (PyYAML-backed)
# ----------------------------------------------------------------------


class TestParseFrontmatterYaml(unittest.TestCase):
    def test_no_frontmatter_returns_body(self) -> None:
        out = parse_frontmatter("just a markdown body\nwith lines")
        self.assertEqual(out.frontmatter, {})
        self.assertEqual(out.body, "just a markdown body\nwith lines")

    def test_basic_scalars(self) -> None:
        out = parse_frontmatter("---\ndescription: hi\nversion: 1\n---\nbody")
        self.assertEqual(out.frontmatter["description"], "hi")
        # PyYAML coerces numeric-looking values to int
        self.assertEqual(out.frontmatter["version"], 1)
        self.assertEqual(out.body, "body")

    def test_inline_list(self) -> None:
        out = parse_frontmatter(
            "---\nallowed-tools: [Bash, Read]\n---\nbody"
        )
        self.assertEqual(out.frontmatter["allowed-tools"], ["Bash", "Read"])

    def test_hyphen_list(self) -> None:
        out = parse_frontmatter(
            "---\nallowed-tools:\n  - Bash\n  - Read\n---\nbody"
        )
        self.assertEqual(out.frontmatter["allowed-tools"], ["Bash", "Read"])

    def test_nested_dict_hooks(self) -> None:
        text = (
            "---\n"
            "hooks:\n"
            "  PostToolUse:\n"
            "    - matcher: Write\n"
            "      hooks:\n"
            "        - type: command\n"
            "          command: ./fmt.sh\n"
            "---\n"
            "body\n"
        )
        out = parse_frontmatter(text)
        self.assertIn("hooks", out.frontmatter)
        self.assertIsInstance(out.frontmatter["hooks"], dict)
        post = out.frontmatter["hooks"]["PostToolUse"]
        self.assertEqual(post[0]["matcher"], "Write")
        self.assertEqual(post[0]["hooks"][0]["type"], "command")

    def test_multiline_string_block(self) -> None:
        text = (
            "---\n"
            "description: |\n"
            "  multi\n"
            "  line\n"
            "---\n"
            "body"
        )
        out = parse_frontmatter(text)
        # YAML's ``|`` keeps interior newlines; the trailing newline is
        # stripped because ``parse_frontmatter`` joins with ``\n`` and the
        # frontmatter ends right at ``---``.
        self.assertEqual(out.frontmatter["description"], "multi\nline")

    def test_quoted_string(self) -> None:
        out = parse_frontmatter(
            '---\ndescription: "value with: colon"\n---\nbody'
        )
        self.assertEqual(out.frontmatter["description"], "value with: colon")

    def test_empty_frontmatter(self) -> None:
        out = parse_frontmatter("---\n---\nbody")
        self.assertEqual(out.frontmatter, {})
        self.assertEqual(out.body, "body")

    def test_unclosed_frontmatter_keeps_body(self) -> None:
        out = parse_frontmatter("---\ndescription: hi\nbody-line")
        # No closing ``---`` → treat as no frontmatter, return raw text.
        self.assertEqual(out.frontmatter, {})

    def test_malformed_yaml_does_not_crash(self) -> None:
        # Should not raise; should return empty frontmatter.
        out = parse_frontmatter("---\nfoo: : bad\n---\nbody")
        self.assertEqual(out.frontmatter, {})
        self.assertEqual(out.body, "body")


# ----------------------------------------------------------------------
# Description extractor + coercer
# ----------------------------------------------------------------------


class TestDescriptionFallbacks(unittest.TestCase):
    def test_first_non_empty_line_used(self) -> None:
        out = _extract_description_from_markdown(
            "\n\nFirst line text\nSecond\n", "default"
        )
        self.assertEqual(out, "First line text")

    def test_heading_stripped(self) -> None:
        out = _extract_description_from_markdown("# A Heading\nbody", "default")
        self.assertEqual(out, "A Heading")

    def test_truncates_long(self) -> None:
        long = "x" * 200
        out = _extract_description_from_markdown(long, "default")
        self.assertEqual(len(out), 100)
        self.assertTrue(out.endswith("..."))

    def test_default_when_blank(self) -> None:
        self.assertEqual(_extract_description_from_markdown("", "fallback"), "fallback")
        self.assertEqual(
            _extract_description_from_markdown("\n\n  \n", "fallback"), "fallback"
        )

    def test_coerce_description_uses_frontmatter(self) -> None:
        desc, has = _coerce_description("explicit", "ignored", "name")
        self.assertEqual(desc, "explicit")
        self.assertTrue(has)

    def test_coerce_description_falls_back(self) -> None:
        desc, has = _coerce_description(None, "First body line\nrest", "skill-x")
        self.assertEqual(desc, "First body line")
        self.assertFalse(has)

    def test_coerce_description_default_fallback(self) -> None:
        desc, has = _coerce_description(None, "", "skill-x")
        self.assertEqual(desc, "Skill: skill-x")
        self.assertFalse(has)


# ----------------------------------------------------------------------
# Field validators
# ----------------------------------------------------------------------


class TestModelCoercion(unittest.TestCase):
    def test_inherit_returns_none(self) -> None:
        self.assertIsNone(_coerce_model("inherit"))

    def test_alias_kept(self) -> None:
        self.assertEqual(_coerce_model("sonnet"), "sonnet")

    def test_canonical_name_kept_silently(self) -> None:
        with self.assertLogs("src.skills.loader", level="WARNING") as cm:
            # Have to log _something_ for the assertLogs context to pass; emit
            # a sentinel so an unrelated absence of warnings doesn't fail.
            logging.getLogger("src.skills.loader").warning("sentinel")
            self.assertEqual(_coerce_model("claude-opus-4-5"), "claude-opus-4-5")
        # only the sentinel; the valid-canonical model should not warn.
        warnings = [r for r in cm.records if "sentinel" not in r.message]
        self.assertEqual(warnings, [])

    def test_unknown_model_warns(self) -> None:
        with self.assertLogs("src.skills.loader", level="WARNING") as cm:
            out = _coerce_model("totally-bogus")
        self.assertEqual(out, "totally-bogus")  # kept; user might know best
        self.assertTrue(any("not a recognized" in r.message for r in cm.records))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(_coerce_model(""))
        self.assertIsNone(_coerce_model(None))


class TestEffortCoercion(unittest.TestCase):
    def test_levels_lowercased(self) -> None:
        for v in EFFORT_LEVELS:
            self.assertEqual(_coerce_effort(v.upper()), v)

    def test_integer_kept_as_string(self) -> None:
        self.assertEqual(_coerce_effort(5), "5")
        self.assertEqual(_coerce_effort("7"), "7")

    def test_invalid_warns_and_drops(self) -> None:
        with self.assertLogs("src.skills.loader", level="WARNING") as cm:
            out = _coerce_effort("insane")
        self.assertIsNone(out)
        self.assertTrue(any("not a valid level" in r.message for r in cm.records))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(_coerce_effort(None))
        self.assertIsNone(_coerce_effort(""))


class TestShellCoercion(unittest.TestCase):
    def test_bash_accepted(self) -> None:
        self.assertEqual(_coerce_shell("bash"), "bash")
        self.assertEqual(_coerce_shell("BASH"), "bash")

    def test_powershell_accepted(self) -> None:
        self.assertEqual(_coerce_shell("powershell"), "powershell")

    def test_invalid_warns_and_returns_none(self) -> None:
        with self.assertLogs("src.skills.loader", level="WARNING") as cm:
            out = _coerce_shell("zsh")
        self.assertIsNone(out)
        self.assertTrue(any("not recognized" in r.message for r in cm.records))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(_coerce_shell(None))
        self.assertIsNone(_coerce_shell(""))


class TestAllowedToolsCoercion(unittest.TestCase):
    def test_arg_patterns_preserved(self) -> None:
        out = _coerce_allowed_tools(["Bash(git diff:*)", "Read"])
        self.assertEqual(out, ["Bash(git diff:*)", "Read"])

    def test_string_form(self) -> None:
        out = _coerce_allowed_tools("Bash, Read, Grep")
        self.assertEqual(out, ["Bash", "Read", "Grep"])

    def test_empty_inputs(self) -> None:
        self.assertEqual(_coerce_allowed_tools(None), [])
        self.assertEqual(_coerce_allowed_tools(""), [])
        self.assertEqual(_coerce_allowed_tools([]), [])


class TestHooksCoercion(unittest.TestCase):
    def test_valid_shape(self) -> None:
        hooks = {
            "PostToolUse": [
                {
                    "matcher": "Write",
                    "hooks": [{"type": "command", "command": "./fmt.sh"}],
                }
            ]
        }
        out = _coerce_hooks(hooks, skill_name="x")
        self.assertEqual(out, hooks)

    def test_unknown_event_drops(self) -> None:
        hooks = {"NotAnEvent": [{"hooks": [{"type": "command"}]}]}
        out = _coerce_hooks(hooks, skill_name="x")
        self.assertIsNone(out)

    def test_missing_inner_hooks_list(self) -> None:
        hooks = {"PostToolUse": [{"matcher": "Write"}]}  # no `hooks` key
        out = _coerce_hooks(hooks, skill_name="x")
        self.assertIsNone(out)

    def test_inner_hook_missing_type(self) -> None:
        hooks = {
            "PostToolUse": [{"hooks": [{"command": "./x.sh"}]}]  # no `type`
        }
        out = _coerce_hooks(hooks, skill_name="x")
        self.assertIsNone(out)

    def test_non_dict_input(self) -> None:
        self.assertIsNone(_coerce_hooks("not a dict", skill_name="x"))
        self.assertIsNone(_coerce_hooks([], skill_name="x"))

    def test_none_input(self) -> None:
        self.assertIsNone(_coerce_hooks(None, skill_name="x"))


# ----------------------------------------------------------------------
# Integration: parse_skill_frontmatter_fields dispatches to validators
# ----------------------------------------------------------------------


class TestParseSkillFrontmatterFields(unittest.TestCase):
    def test_hooks_round_trip(self) -> None:
        fm = {
            "description": "hi",
            "hooks": {
                "PostToolUse": [
                    {"hooks": [{"type": "command", "command": "./x.sh"}]}
                ]
            },
        }
        parsed = parse_skill_frontmatter_fields(fm, "body", "x")
        self.assertIsNotNone(parsed["hooks"])
        self.assertIn("PostToolUse", parsed["hooks"])

    def test_invalid_hooks_dropped_silently(self) -> None:
        # AC#6: invalid shape → hooks is None, no exception
        fm = {"description": "hi", "hooks": {"PostToolUse": [{"matcher": "X"}]}}
        parsed = parse_skill_frontmatter_fields(fm, "body", "x")
        self.assertIsNone(parsed["hooks"])

    def test_shell_passes_through(self) -> None:
        fm = {"description": "hi", "shell": "powershell"}
        parsed = parse_skill_frontmatter_fields(fm, "body", "x")
        self.assertEqual(parsed["shell"], "powershell")

    def test_model_inherit_to_none(self) -> None:
        fm = {"description": "hi", "model": "inherit"}
        parsed = parse_skill_frontmatter_fields(fm, "body", "x")
        self.assertIsNone(parsed["model"])

    def test_effort_int_preserved(self) -> None:
        fm = {"description": "hi", "effort": 5}
        parsed = parse_skill_frontmatter_fields(fm, "body", "x")
        self.assertEqual(parsed["effort"], "5")

    def test_effort_invalid_dropped(self) -> None:
        fm = {"description": "hi", "effort": "insane"}
        parsed = parse_skill_frontmatter_fields(fm, "body", "x")
        self.assertIsNone(parsed["effort"])

    def test_description_falls_back_to_first_line(self) -> None:
        fm = {}  # no description
        body = "First line of the body\nsecond"
        parsed = parse_skill_frontmatter_fields(fm, body, "x")
        self.assertEqual(parsed["description"], "First line of the body")
        self.assertFalse(parsed["has_user_specified_description"])

    def test_allowed_tools_with_arg_pattern(self) -> None:
        fm = {"allowed-tools": ["Bash(git diff:*)", "Read"]}
        parsed = parse_skill_frontmatter_fields(fm, "", "x")
        self.assertEqual(parsed["allowed_tools"], ["Bash(git diff:*)", "Read"])


# ----------------------------------------------------------------------
# End-to-end via load_skills_from_skills_dir — disk SKILL.md path
# ----------------------------------------------------------------------


class TestLoadSkillsWithFrontmatter(unittest.TestCase):
    def test_skill_carries_hooks_and_shell(self, *, tmp_path=None) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            sk = base / "demo"
            sk.mkdir()
            (sk / "SKILL.md").write_text(
                "---\n"
                "description: demo\n"
                "shell: powershell\n"
                "hooks:\n"
                "  PostToolUse:\n"
                "    - matcher: Write\n"
                "      hooks:\n"
                "        - type: command\n"
                "          command: ./fmt.sh\n"
                "---\n"
                "body\n"
            )
            skills = load_skills_from_skills_dir(str(base), "projectSettings")
            self.assertEqual(len(skills), 1)
            s = skills[0]
            self.assertEqual(s.shell, "powershell")
            self.assertIsNotNone(s.hooks)
            self.assertIn("PostToolUse", s.hooks)
            self.assertEqual(
                s.hooks["PostToolUse"][0]["hooks"][0]["command"], "./fmt.sh"
            )


if __name__ == "__main__":
    unittest.main()

"""Tests for python-frontmatter adapter (Task #2)."""

from __future__ import annotations

import pytest

from src.skills._frontmatter_adapter import (
    is_frontmatter_available,
    parse_frontmatter_with_library,
)
from src.skills.frontmatter import FrontmatterParseResult


class TestFrontmatterAvailable:
    def test_frontmatter_is_available(self):
        assert is_frontmatter_available() is True


class TestParseFrontmatterWithLibrary:
    def test_simple_frontmatter(self):
        markdown = '''---
description: Test skill
---
This is the body.
'''
        result = parse_frontmatter_with_library(markdown)
        assert result.frontmatter.get("description") == "Test skill"
        assert result.body.strip() == "This is the body."

    def test_frontmatter_with_nested_structures(self):
        markdown = '''---
description: A skill with hooks
hooks:
  PostToolUse:
    - matcher: Write
      hooks:
        - type: command
          command: ./scripts/format.sh
---
Skill body content.
'''
        result = parse_frontmatter_with_library(markdown)
        assert "hooks" in result.frontmatter
        assert result.frontmatter["hooks"]["PostToolUse"][0]["matcher"] == "Write"

    def test_empty_frontmatter(self):
        markdown = '''---
---
Body without frontmatter.
'''
        result = parse_frontmatter_with_library(markdown)
        assert result.frontmatter == {}
        assert "Body without frontmatter" in result.body

    def test_no_frontmatter(self):
        markdown = '''# Just a markdown file
No frontmatter here.
'''
        result = parse_frontmatter_with_library(markdown)
        assert result.frontmatter == {}
        assert "Just a markdown file" in result.body

    def test_empty_input(self):
        result = parse_frontmatter_with_library("")
        assert result.frontmatter == {}
        assert result.body == ""

    def test_frontmatter_with_list_values(self):
        markdown = '''---
description: Multi-tool skill
allowed-tools:
  - Bash(git status:*)
  - Read
---
Body.
'''
        result = parse_frontmatter_with_library(markdown)
        allowed = result.frontmatter.get("allowed-tools", [])
        assert len(allowed) == 2
        assert "Bash(git status:*)" in allowed

    def test_frontmatter_with_shell_block(self):
        markdown = '''---
description: Shell skill
shell:
  command: echo test
  timeout: 30
---
Body.
'''
        result = parse_frontmatter_with_library(markdown)
        assert result.frontmatter.get("shell", {}).get("command") == "echo test"
        assert result.frontmatter.get("shell", {}).get("timeout") == 30


class TestBackwardCompatibility:
    def test_result_type_matches_original(self):
        """Ensure adapter returns same type as original parse_frontmatter."""
        markdown = '''---
key: value
---
body
'''
        result = parse_frontmatter_with_library(markdown)
        assert isinstance(result, FrontmatterParseResult)
        assert "key" in result.frontmatter
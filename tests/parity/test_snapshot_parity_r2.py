"""Phase D — Round 2 Snapshot Parity Tests.

Compare deterministic outputs for fixed inputs across 6 categories:
1. System prompt generation
2. Message normalization
3. Token estimation
4. Cost calculation
5. Bash command classification
6. Permission rule matching
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import (
    AssistantMessage,
    UserMessage,
    create_assistant_message,
    create_user_message,
    message_to_dict,
    normalize_messages_for_api,
)


# ---------------------------------------------------------------------------
# 1. System prompt generation snapshot
# ---------------------------------------------------------------------------

class TestSystemPromptSnapshot(unittest.TestCase):
    """Deterministic system prompt output for fixed context."""

    def test_append_system_context_deterministic(self) -> None:
        from src.context_system.prompt_assembly import append_system_context

        prompt = ["You are Claude, a helpful AI assistant."]
        context = {
            "gitStatus": "On branch main\nChanges not staged:\n  modified: src/app.py",
        }
        result = append_system_context(prompt, context)

        # Should contain both the base prompt and git status
        self.assertIn("Claude", result)
        self.assertIn("main", result)
        self.assertIn("src/app.py", result)

        # Running again should produce identical output
        result2 = append_system_context(prompt, context)
        self.assertEqual(result, result2)

    def test_append_empty_context_unchanged(self) -> None:
        from src.context_system.prompt_assembly import append_system_context

        prompt = ["Base prompt only."]
        result = append_system_context(prompt, {})
        self.assertIn("Base prompt only", result)

    def test_prompt_parts_dataclass_stable(self) -> None:
        from src.context_system.models import SystemPromptParts

        parts = SystemPromptParts(
            default_system_prompt=["identity", "tools", "env"],
            user_context={"claudeMd": "Be concise.", "currentDate": "2025-01-15"},
            system_context={"gitStatus": "On branch develop"},
        )
        self.assertEqual(len(parts.default_system_prompt), 3)
        self.assertEqual(parts.user_context["currentDate"], "2025-01-15")


# ---------------------------------------------------------------------------
# 2. Message normalization snapshot
# ---------------------------------------------------------------------------

class TestMessageNormalizationSnapshot(unittest.TestCase):
    """Deterministic API message output for fixed message sequence."""

    def _build_fixed_conversation(self) -> list:
        return [
            create_user_message("Hello, Claude!"),
            AssistantMessage(
                content=[TextBlock(text="Hello! How can I help?")],
                stop_reason="end_turn",
            ),
            create_user_message("Read the file src/app.py"),
            AssistantMessage(
                content=[
                    TextBlock(text="Reading the file."),
                    ToolUseBlock(id="tu_123", name="Read", input={"file_path": "src/app.py"}),
                ],
                stop_reason="tool_use",
            ),
            create_user_message(
                [ToolResultBlock(tool_use_id="tu_123", content="def main():\n    pass")],
            ),
            create_assistant_message("The file contains a simple main function."),
        ]

    def test_normalize_produces_consistent_structure(self) -> None:
        msgs = self._build_fixed_conversation()
        api_msgs = normalize_messages_for_api(msgs)

        self.assertEqual(len(api_msgs), 6)
        roles = [m["role"] for m in api_msgs]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant", "user", "assistant"])

    def test_normalize_alternating_roles(self) -> None:
        msgs = self._build_fixed_conversation()
        api_msgs = normalize_messages_for_api(msgs)
        for i in range(len(api_msgs) - 1):
            self.assertNotEqual(
                api_msgs[i]["role"], api_msgs[i + 1]["role"],
                f"Adjacent messages {i} and {i+1} have same role",
            )

    def test_tool_use_block_structure(self) -> None:
        msgs = self._build_fixed_conversation()
        api_msgs = normalize_messages_for_api(msgs)
        # 4th message (index 3) should have tool_use
        tool_msg = api_msgs[3]
        tool_blocks = [b for b in tool_msg["content"] if b.get("type") == "tool_use"]
        self.assertEqual(len(tool_blocks), 1)
        self.assertEqual(tool_blocks[0]["name"], "Read")
        self.assertEqual(tool_blocks[0]["id"], "tu_123")

    def test_tool_result_block_structure(self) -> None:
        msgs = self._build_fixed_conversation()
        api_msgs = normalize_messages_for_api(msgs)
        # 5th message (index 4) should have tool_result
        result_msg = api_msgs[4]
        result_blocks = [b for b in result_msg["content"] if b.get("type") == "tool_result"]
        self.assertEqual(len(result_blocks), 1)
        self.assertEqual(result_blocks[0]["tool_use_id"], "tu_123")

    def test_message_to_dict_stable(self) -> None:
        msg = create_user_message("Stable message")
        d1 = message_to_dict(msg)
        d2 = message_to_dict(msg)
        # Same input should produce same output (except timestamp/uuid which are set at creation)
        self.assertEqual(d1["role"], d2["role"])
        self.assertEqual(d1["content"], d2["content"])
        self.assertEqual(d1["uuid"], d2["uuid"])


# ---------------------------------------------------------------------------
# 3. Token estimation snapshot
# ---------------------------------------------------------------------------

class TestTokenEstimationSnapshot(unittest.TestCase):
    """Deterministic token estimates for fixed messages."""

    def test_fixed_text_estimation(self) -> None:
        from src.token_estimation import rough_token_count_estimation

        # Fixed inputs → deterministic outputs
        cases = [
            ("Hello, world!", 4, 3),  # 13 chars / 4 = 3
            ("a" * 100, 4, 25),       # 100 / 4 = 25
            ("", 4, 0),               # empty = 0
            ('{"key": "value"}', 2, 8),  # 16 chars / 2 = 8 (JSON)
        ]
        for text, bpt, expected in cases:
            result = rough_token_count_estimation(text, bytes_per_token=bpt)
            self.assertEqual(result, expected, f"Failed for text len {len(text)}, bpt={bpt}")

    def test_file_type_estimation(self) -> None:
        from src.token_estimation import rough_token_count_estimation_for_file_type

        # JSON files use 2 bytes/token
        json_content = '{"key": "value", "nested": {"a": 1}}'
        json_tokens = rough_token_count_estimation_for_file_type(json_content, "json")

        # Python files use 4 bytes/token
        py_content = 'def hello():\n    print("hello")\n'
        py_tokens = rough_token_count_estimation_for_file_type(py_content, "py")

        # JSON should have more tokens per character
        self.assertGreater(json_tokens, 0)
        self.assertGreater(py_tokens, 0)

    def test_count_tokens_deterministic(self) -> None:
        from src.token_estimation import count_tokens

        text = "The quick brown fox jumps over the lazy dog."
        t1 = count_tokens(text)
        t2 = count_tokens(text)
        self.assertEqual(t1, t2)
        self.assertGreater(t1, 0)


# ---------------------------------------------------------------------------
# 4. Cost calculation snapshot
# ---------------------------------------------------------------------------

class TestCostCalculationSnapshot(unittest.TestCase):
    """Deterministic cost calculation for fixed usage."""

    def test_cost_tracker_accumulation(self) -> None:
        from src.services.cost_tracker import CostTracker

        tracker = CostTracker()

        # Fixed usage events
        tracker.record_usage("claude-sonnet-4-6", {"input_tokens": 1000, "output_tokens": 500})
        tracker.record_usage("claude-sonnet-4-6", {"input_tokens": 2000, "output_tokens": 800})

        self.assertGreater(tracker.get_total_cost(), 0)
        self.assertEqual(tracker.get_total_input_tokens(), 3000)
        self.assertEqual(tracker.get_total_output_tokens(), 1300)

    def test_per_model_aggregation(self) -> None:
        from src.services.cost_tracker import CostTracker

        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50})
        tracker.record_usage("claude-haiku-3", {"input_tokens": 200, "output_tokens": 100})

        model_usage = tracker.get_model_usage()
        self.assertIn("claude-sonnet-4-6", model_usage)
        self.assertIn("claude-haiku-3", model_usage)
        self.assertEqual(model_usage["claude-sonnet-4-6"].input_tokens, 100)
        self.assertEqual(model_usage["claude-haiku-3"].input_tokens, 200)

    def test_cost_tracker_turn_reset(self) -> None:
        from src.services.cost_tracker import CostTracker

        tracker = CostTracker()
        tracker.record_usage("test", {"input_tokens": 100, "output_tokens": 50})
        self.assertGreater(tracker.get_total_cost(), 0)
        self.assertGreater(tracker.get_turn_cost(), 0)

        tracker.reset_turn()
        self.assertEqual(tracker.get_turn_cost(), 0)
        # Total should remain
        self.assertGreater(tracker.get_total_cost(), 0)


# ---------------------------------------------------------------------------
# 5. Bash command classification snapshot
# ---------------------------------------------------------------------------

class TestBashClassificationSnapshot(unittest.TestCase):
    """Deterministic classification for a fixed set of commands."""

    EXPECTED_CLASSIFICATIONS = {
        "echo hello": "safe",
        "pwd": "safe",
        "true": "safe",
        "cat file.txt": "read_only",
        "ls -la": "read_only",
        "grep pattern file": "read_only",
        "git status": "read_only",
        "cp a b": "write",
        "mv old new": "write",
        "mkdir -p dir": "write",
    }

    def test_all_classifications_match(self) -> None:
        from src.permissions.bash_security import analyze_bash_command

        mismatches = []
        for cmd, expected in self.EXPECTED_CLASSIFICATIONS.items():
            result = analyze_bash_command(cmd)
            if result.safety != expected:
                mismatches.append(f"{cmd}: expected={expected}, got={result.safety}")

        self.assertEqual(mismatches, [], f"Classification mismatches: {mismatches}")

    def test_classification_deterministic(self) -> None:
        from src.permissions.bash_security import analyze_bash_command

        for cmd in self.EXPECTED_CLASSIFICATIONS:
            r1 = analyze_bash_command(cmd)
            r2 = analyze_bash_command(cmd)
            self.assertEqual(r1.safety, r2.safety, f"Non-deterministic for: {cmd}")


# ---------------------------------------------------------------------------
# 6. Permission rule matching snapshot
# ---------------------------------------------------------------------------

class TestPermissionRuleMatchingSnapshot(unittest.TestCase):
    """Deterministic permission rule parsing and matching."""

    def test_rule_parsing_roundtrip(self) -> None:
        from src.permissions.rule_parser import (
            permission_rule_value_from_string,
            permission_rule_value_to_string,
        )

        test_cases = [
            ("Bash", "Bash", None),
            ("Read(file.txt)", "Read", "file.txt"),
            ("Write(src/app.py)", "Write", "src/app.py"),
            ("Bash(ls -la)", "Bash", "ls -la"),
        ]
        for rule_str, expected_tool, expected_content in test_cases:
            parsed = permission_rule_value_from_string(rule_str)
            self.assertEqual(parsed.tool_name, expected_tool, f"Tool mismatch for: {rule_str}")
            self.assertEqual(parsed.rule_content, expected_content, f"Content mismatch for: {rule_str}")

            # Roundtrip
            serialized = permission_rule_value_to_string(parsed)
            reparsed = permission_rule_value_from_string(serialized)
            self.assertEqual(reparsed.tool_name, parsed.tool_name)
            self.assertEqual(reparsed.rule_content, parsed.rule_content)

    def test_dangerous_bash_permission_detection(self) -> None:
        from src.permissions.bash_security import is_dangerous_bash_permission

        dangerous_cases = [
            ("Bash", None, True),     # wildcard
            ("Bash", "*", True),      # explicit wildcard
            ("Bash", "python", True), # code execution
            ("Bash", "node", True),   # code execution
            ("Bash", "sudo", True),   # elevated privilege
        ]
        for tool_name, content, expected in dangerous_cases:
            result = is_dangerous_bash_permission(tool_name, content)
            self.assertEqual(
                result, expected,
                f"is_dangerous_bash_permission({tool_name!r}, {content!r}) = {result}, expected {expected}",
            )

    def test_safe_bash_permissions(self) -> None:
        from src.permissions.bash_security import is_dangerous_bash_permission

        safe_cases = [
            ("Bash", "ls -la", False),
            ("Bash", "cat file.txt", False),
            ("Bash", "grep pattern", False),
            ("Read", None, False),       # Not Bash tool
            ("Write", "*", False),       # Not Bash tool
        ]
        for tool_name, content, expected in safe_cases:
            result = is_dangerous_bash_permission(tool_name, content)
            self.assertEqual(
                result, expected,
                f"is_dangerous_bash_permission({tool_name!r}, {content!r}) = {result}, expected {expected}",
            )

    def test_filesystem_safety_snapshot(self) -> None:
        from src.permissions.filesystem import check_path_safety_for_auto_edit

        # Protected files require confirmation
        protected = [".gitconfig", ".bashrc", ".env", "package-lock.json"]
        for f in protected:
            result = check_path_safety_for_auto_edit(f"/project/{f}")
            self.assertIsNotNone(result, f"Expected protection for: {f}")
            self.assertEqual(result.behavior, "ask")

        # Normal files auto-allowed
        normal = ["app.py", "README.md", "index.ts"]
        for f in normal:
            result = check_path_safety_for_auto_edit(f"/project/src/{f}")
            self.assertIsNone(result, f"Expected auto-allow for: {f}")

    def test_legacy_tool_name_normalization(self) -> None:
        from src.permissions.rule_parser import normalize_legacy_tool_name

        self.assertEqual(normalize_legacy_tool_name("Task"), "Agent")
        self.assertEqual(normalize_legacy_tool_name("KillShell"), "TaskStop")
        self.assertEqual(normalize_legacy_tool_name("Bash"), "Bash")  # No alias


if __name__ == "__main__":
    unittest.main()

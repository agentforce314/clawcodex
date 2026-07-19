"""Unit tests for src/query/tool_failure_loop_guard.py.

Each case mirrors a specific behavior of TS query/toolFailureLoopGuard.ts
(line refs in the test docstrings refer to that file).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.query.tool_failure_loop_guard import (
    DEFAULT_TOOL_FAILURE_LOOP_THRESHOLD,
    MAX_FALLBACK_CATEGORY_LENGTH,
    create_tool_failure_loop_guard_state,
    get_tool_failure_loop_threshold,
    update_tool_failure_loop_guard,
    _extract_normalized_path,
    _is_ignored_synthetic_tool_result,
    _normalize_error_category,
    _normalize_path,
)
from src.types.content_blocks import ToolResultBlock, ToolUseBlock
from src.types.messages import AttachmentMessage, UserMessage


def _tool_use(block_id: str, name: str = "Read", input: dict | None = None) -> ToolUseBlock:
    return ToolUseBlock(id=block_id, name=name, input=input or {})


def _result(block_id: str, content: str, *, is_error: bool = True) -> UserMessage:
    return UserMessage(
        content=[
            ToolResultBlock(tool_use_id=block_id, content=content, is_error=is_error)
        ]
    )


def _update(state, blocks, results, threshold=None):
    return update_tool_failure_loop_guard(
        state=state,
        tool_use_blocks=blocks,
        tool_results=results,
        threshold=threshold,
    )


class TestThreshold(unittest.TestCase):
    def test_default_when_unset(self):
        self.assertEqual(get_tool_failure_loop_threshold(None), 3)

    def test_env_read_at_call_time(self):
        """guard:34-35 — TS reads process.env per call; the port must too."""
        with patch.dict(
            "os.environ",
            {"CLAUDE_CODE_TOOL_FAILURE_LOOP_THRESHOLD": "5"},
        ):
            self.assertEqual(get_tool_failure_loop_threshold(), 5)
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CLAUDE_CODE_TOOL_FAILURE_LOOP_THRESHOLD", None)
            self.assertEqual(get_tool_failure_loop_threshold(), 3)

    def test_valid_numeric_string(self):
        self.assertEqual(get_tool_failure_loop_threshold("7"), 7)
        self.assertEqual(get_tool_failure_loop_threshold(" 4 "), 4)

    def test_non_numeric_falls_back(self):
        """guard:41-44 — non-^\\d+$ → default."""
        for bad in ("abc", "-1", "3.5", "+3", "", "  "):
            self.assertEqual(
                get_tool_failure_loop_threshold(bad),
                DEFAULT_TOOL_FAILURE_LOOP_THRESHOLD,
                msg=f"value={bad!r}",
            )

    def test_unsafe_integer_falls_back(self):
        """guard:47-49 — Number.isSafeInteger bound = 2**53 - 1."""
        self.assertEqual(
            get_tool_failure_loop_threshold(str(2**53)),
            DEFAULT_TOOL_FAILURE_LOOP_THRESHOLD,
        )
        self.assertEqual(get_tool_failure_loop_threshold(str(2**53 - 1)), 2**53 - 1)

    def test_zero_disables_guard(self):
        """guard:59-61 — threshold 0 short-circuits to not-tripped."""
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("t1", "Bash")]
        for _ in range(100):
            decision = _update(
                state, blocks, [_result("t1", "boom failure")], threshold=0
            )
            self.assertFalse(decision.tripped)
        self.assertEqual(state.signature_counts, {})

    def test_explicit_negative_or_non_int_threshold_falls_back(self):
        """guard:170-178 — explicit kwarg that is negative/non-int → DEFAULT,
        not the env value."""
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("t1", "Bash")]
        # threshold=-2 normalizes to default 3 → trips on 3rd failure.
        for i in range(2):
            self.assertFalse(
                _update(state, blocks, [_result("t1", "same err")], threshold=-2).tripped
            )
        self.assertTrue(
            _update(state, blocks, [_result("t1", "same err")], threshold=-2).tripped
        )


class TestSuccessReset(unittest.TestCase):
    def test_same_tool_success_resets_its_persistent_counter(self):
        """A success resets persistent failures for that tool."""
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("t1", "Bash")]
        for _ in range(2):
            self.assertFalse(
                _update(state, blocks, [_result("t1", "same err")]).tripped
            )
        # Success batch — resets.
        self.assertFalse(
            _update(state, blocks, [_result("t1", "ok", is_error=False)]).tripped
        )
        self.assertEqual(state.signature_counts, {})
        self.assertEqual(state.persistent_signature_counts, {})
        # Two more failures still under threshold.
        for _ in range(2):
            self.assertFalse(
                _update(state, blocks, [_result("t1", "same err")]).tripped
            )

    def test_within_batch_failures_accumulate_per_failure(self):
        """Counters increment per FAILURE, not per batch (guard:96-107):
        2 identical failures in one batch + 1 in the next → trips at 3."""
        state = create_tool_failure_loop_guard_state()
        first_batch_blocks = [_tool_use("t1", "Bash"), _tool_use("t2", "Bash")]
        decision = _update(
            state,
            first_batch_blocks,
            [_result("t1", "same err"), _result("t2", "same err")],
        )
        self.assertFalse(decision.tripped)
        self.assertEqual(state.signature_counts["Bash\0same err"], 2)
        decision = _update(
            state, [_tool_use("t3", "Bash")], [_result("t3", "same err")]
        )
        self.assertTrue(decision.tripped)
        self.assertEqual(decision.kind, "signature")

    def test_mixed_batch_counts_failure_for_a_different_tool(self):
        """An unrelated success must not conceal a repeated failure."""
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("t1", "Bash"), _tool_use("t2", "Read")]
        for attempt in range(3):
            decision = _update(
                state,
                blocks,
                [
                    _result("t1", "same err"),
                    _result("t2", "fine", is_error=False),
                ],
            )
            self.assertEqual(decision.tripped, attempt == 2)
        self.assertEqual(decision.tool_name, "Bash")


class TestIgnoredSynthetics(unittest.TestCase):
    CASES = [
        "Interrupted by user",
        "[Request interrupted by user]",
        "Request interrupted by user for tool use",
        "Error: user rejected tool use",
        "The user doesn't want to proceed with this tool use. The tool use was rejected.",
        "The user doesn't want to take this action right now.",
        "Streaming fallback - tool execution discarded",
        "Cancelled: parallel tool call aborted",
    ]

    def test_patterns_are_ignored(self):
        """guard:237-255 — all seven synthetic shapes (incl. [..] wrap and
        error: prefix) are neither failures nor successes."""
        for text in self.CASES:
            with self.subTest(text=text):
                self.assertTrue(_is_ignored_synthetic_tool_result(text))

    def test_ignored_results_neither_trip_nor_reset(self):
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("t1", "Bash")]
        for _ in range(2):
            _update(state, blocks, [_result("t1", "same err")])
        # Ignored synthetic: no reset, no increment.
        decision = _update(
            state, blocks, [_result("t1", "[Request interrupted by user]")]
        )
        self.assertFalse(decision.tripped)
        self.assertEqual(state.signature_counts.get("Bash\0same err"), 2)
        # Next real failure trips (count reaches 3).
        self.assertTrue(_update(state, blocks, [_result("t1", "same err")]).tripped)

    def test_real_error_is_not_ignored(self):
        self.assertFalse(_is_ignored_synthetic_tool_result("ENOENT: no file"))


class TestCategoryNormalization(unittest.TestCase):
    def test_named_categories(self):
        """guard:257-286 regex ladder."""
        cases = [
            ("InputValidationError: bad field", "InputValidationError"),
            ("Invalid tool parameters supplied", "InputValidationError"),
            ("No such tool available: Foo", "NoSuchTool"),
            ('{"error": "unknown tool: Foo"}', "NoSuchTool"),  # port-native
            ("EACCES on /etc/passwd", "PermissionError"),
            ("operation failed: permission denied", "PermissionError"),
            ("ENOENT: missing", "NotFound"),
            ("file not found", "NotFound"),
            ("[Errno 2] No such file or directory: 'x'", "NotFound"),  # port-native
            ("Error writing file /tmp/x", "FileWriteError"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(_normalize_error_category(text), expected)

    def test_fallback_truncates_to_120_lower(self):
        text = "Z" * 300
        out = _normalize_error_category(text)
        self.assertEqual(out, "z" * MAX_FALLBACK_CATEGORY_LENGTH)

    def test_empty_is_unknown_error(self):
        self.assertEqual(_normalize_error_category(""), "unknown error")

    def test_tool_use_error_tags_stripped(self):
        """guard:288-293 — <tool_use_error> tags removed, whitespace
        collapsed before matching."""
        self.assertEqual(
            _normalize_error_category(
                "<tool_use_error>permission   denied</tool_use_error>"
            ),
            "PermissionError",
        )


class TestPathHandling(unittest.TestCase):
    def test_field_precedence(self):
        """guard:301 — file_path, then path, then notebook_path."""
        self.assertEqual(
            _extract_normalized_path(
                {"file_path": "/a/b", "path": "/c", "notebook_path": "/d"}
            ),
            "/a/b",
        )
        self.assertEqual(
            _extract_normalized_path({"path": "/c", "notebook_path": "/d"}), "/c"
        )
        self.assertEqual(_extract_normalized_path({"notebook_path": "/d"}), "/d")

    def test_non_dict_and_non_str_values(self):
        self.assertIsNone(_extract_normalized_path(None))
        self.assertIsNone(_extract_normalized_path("string"))
        self.assertIsNone(_extract_normalized_path({"file_path": 42}))
        self.assertIsNone(_extract_normalized_path({}))

    def test_normalization(self):
        """guard:315-323."""
        self.assertEqual(_normalize_path("C:\\x\\y"), "C:/x/y")
        self.assertEqual(_normalize_path("/a//b///c/"), "/a/b/c")
        self.assertEqual(_normalize_path("  /a/b  "), "/a/b")
        self.assertEqual(_normalize_path("///"), "/")

    def test_path_trip(self):
        """Same path across different tools trips kind='path' first
        (guard:109-121)."""
        state = create_tool_failure_loop_guard_state()
        path = "/tmp/target.txt"
        for i, tool in enumerate(["Read", "Write", "Edit"]):
            blocks = [_tool_use(f"t{i}", tool, {"file_path": path})]
            decision = _update(
                state, blocks, [_result(f"t{i}", f"error variant {i}")]
            )
        self.assertTrue(decision.tripped)
        self.assertEqual(decision.kind, "path")
        self.assertEqual(decision.path, path)
        self.assertIn(f"The path `{path}` failed 3 times.", decision.message)


class TestTripPrecedence(unittest.TestCase):
    def test_warns_one_matching_failure_before_stop(self):
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("t1", "Read")]
        first = _update(state, blocks, [_result("t1", "ENOENT: gone")])
        second = _update(state, blocks, [_result("t1", "ENOENT: gone")])
        self.assertEqual(first.advisories, ())
        self.assertEqual(len(second.advisories), 1)
        self.assertIn("failed 2/3 times", second.advisories[0])

    def test_unrelated_success_does_not_hide_repeated_failure(self):
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("bad", "Read"), _tool_use("ok", "Grep")]
        mixed = [
            _result("bad", "ENOENT: gone"),
            _result("ok", "matches", is_error=False),
        ]
        _update(state, blocks, mixed)
        decision = _update(state, blocks, mixed)
        self.assertEqual(len(decision.advisories), 1)
        decision = _update(state, blocks, mixed)
        self.assertTrue(decision.tripped)
        self.assertEqual(decision.tool_name, "Read")

    def test_same_tool_success_resets_persistent_signature(self):
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("t1", "Read")]
        _update(state, blocks, [_result("t1", "ENOENT: gone")])
        _update(state, blocks, [_result("t1", "ok", is_error=False)])
        decision = _update(state, blocks, [_result("t1", "ENOENT: gone")])
        self.assertEqual(decision.advisories, ())

    def test_signature_trip(self):
        """Same tool+category, different paths → kind='signature'
        (guard:123-137)."""
        state = create_tool_failure_loop_guard_state()
        for i in range(3):
            blocks = [
                _tool_use(f"t{i}", "Read", {"file_path": f"/tmp/f{i}.txt"})
            ]
            decision = _update(state, blocks, [_result(f"t{i}", "ENOENT: gone")])
        self.assertTrue(decision.tripped)
        self.assertEqual(decision.kind, "signature")
        self.assertEqual(decision.tool_name, "Read")
        self.assertEqual(decision.error_category, "NotFound")
        self.assertIn("`Read` failed 3 times with `NotFound`.", decision.message)

    def test_category_trip_across_tools(self):
        """Different tools, same category, no paths → kind='category'
        (guard:139-151)."""
        state = create_tool_failure_loop_guard_state()
        for i, tool in enumerate(["Bash", "Grep", "WebFetch"]):
            blocks = [_tool_use(f"t{i}", tool)]
            decision = _update(
                state, blocks, [_result(f"t{i}", "operation: permission denied")]
            )
        self.assertTrue(decision.tripped)
        self.assertEqual(decision.kind, "category")
        self.assertEqual(decision.error_category, "PermissionError")
        self.assertIn(
            "Tool calls failed 3 times with `PermissionError`.", decision.message
        )

    def test_signature_key_uses_nul_separator(self):
        """tool 'a' + category 'b\\0c' must not collide with tool 'a\\0b' +
        category 'c' (guard:97-100)."""
        state = create_tool_failure_loop_guard_state()
        blocks_a = [_tool_use("t1", "a")]
        _update(state, blocks_a, [_result("t1", "b\0c")])
        keys = set(state.signature_counts)
        self.assertEqual(len(keys), 1)
        # The category half is normalized text (control chars survive the
        # whitespace collapse only if non-space); the key must be the
        # tool name, a NUL, then the category.
        key = next(iter(keys))
        self.assertTrue(key.startswith("a\0"))

    def test_unknown_tool_name_when_block_missing(self):
        """tool_use_id with no matching block → toolName 'unknown'
        (guard:81-82)."""
        state = create_tool_failure_loop_guard_state()
        for _ in range(3):
            decision = _update(
                state, [], [_result("orphan", "weird failure text")]
            )
        self.assertTrue(decision.tripped)
        self.assertEqual(decision.kind, "signature")
        self.assertEqual(decision.tool_name, "unknown")


class TestHarvest(unittest.TestCase):
    def test_attachment_messages_skipped(self):
        """guard:192 — only type=='user' messages are harvested."""
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("t1", "Bash")]
        attachment = AttachmentMessage(attachments=[{"type": "whatever"}])
        for _ in range(5):
            decision = _update(state, blocks, [attachment])
            self.assertFalse(decision.tripped)
        self.assertEqual(state.signature_counts, {})

    def test_list_content_blocks_joined(self):
        """guard:219-221 — list content stringifies recursively with
        space join."""
        state = create_tool_failure_loop_guard_state()
        blocks = [_tool_use("t1", "Bash")]
        msg = UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content=[{"type": "text", "text": "permission"},
                             {"type": "text", "text": "denied"}],
                    is_error=True,
                )
            ]
        )
        _update(state, blocks, [msg])
        self.assertIn("Bash\0PermissionError", state.signature_counts)


if __name__ == "__main__":
    unittest.main()

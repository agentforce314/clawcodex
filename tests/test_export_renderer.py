"""Tests for :mod:`src.utils.export_renderer`.

Ports the *pure-renderer* portions of ``typescript/src/utils/exportRenderer.test.ts``:
``renderMessagesToMarkdown`` and ``renderMessagesToJSON``. Plus a fresh
plain-text suite (no TS reference exists — TS's ``text`` format streams Ink,
which is surface-coupled and not line-ported; Python's ``text`` format is a
standalone structured transcript).

Two deliberate adaptations from the TS fixtures (documented for parity review):

* **Terminal-output fixtures skipped.** TS exercises a ``<bash-stdout>`` /
  ``<local-command-stdout>`` / ``<bash-input>`` envelope parser. Python ``src/``
  never emits those wrappers (grep-confirmed zero occurrences), so the parser is
  dead code and was not ported; its fixtures are intentionally absent here.

* **``<command-name>`` -> ``<task-notification>``.** TS's ``INTERNAL_TEXT_TAGS``
  includes nine tags; of those only ``<task-notification>`` is live in Python
  (``src/constants/xml.py`` — emitted by the coordinator as user-role content).
  The "internal text block filtering" fixtures therefore use
  ``<task-notification>`` where TS used ``<command-name>``; the *behavior* under
  test (internal wrappers stripped, surrounding visible text preserved) is
  identical.

Fixtures use the TS wire shape ``{type, message: {role, content}, timestamp}``;
the renderer's dual-mode accessors read this exactly as they read flat Python
dataclasses.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from src.types.messages import (
    CANCEL_MESSAGE,
    INTERRUPT_MESSAGE,
    SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
)
from src.utils.export_renderer import (
    render_messages_to_json,
    render_messages_to_markdown,
    render_messages_to_plain_text,
)


# --------------------------------------------------------------------------- #
# Fixture builders (mirror the TS test's Message-shaped helpers)
# --------------------------------------------------------------------------- #


def user_message(text: str) -> Dict[str, Any]:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "timestamp": "2026-05-13T12:00:00Z",
    }


def assistant_message(text: str) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        "timestamp": "2026-05-13T12:00:01Z",
    }


def tool_use_message(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tool-123", "name": tool_name, "input": tool_input}
            ],
        },
        "timestamp": "2026-05-13T12:00:02Z",
    }


def tool_result_message(content: str, is_error: bool = False) -> Dict[str, Any]:
    return {
        "type": "tool",
        "message": {
            "role": "tool",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-123",
                    "content": content,
                    "is_error": is_error,
                }
            ],
        },
        "timestamp": "2026-05-13T12:00:03Z",
    }


def _parse(messages: List[Any]) -> Dict[str, Any]:
    return json.loads(render_messages_to_json(messages))


# --------------------------------------------------------------------------- #
# renderMessagesToMarkdown
# --------------------------------------------------------------------------- #


class TestRenderMessagesToMarkdown:
    def test_includes_conversation_export_header(self) -> None:
        md = render_messages_to_markdown([user_message("hello")])
        assert "# Conversation Export" in md
        assert "Format: Markdown" in md

    def test_includes_exported_timestamp(self) -> None:
        md = render_messages_to_markdown([])
        assert re.search(r"Exported: \d{4}-\d{2}-\d{2}T", md)

    def test_renders_user_and_assistant_headings(self) -> None:
        md = render_messages_to_markdown([user_message("hello"), assistant_message("world")])
        assert "## User" in md
        assert "## Assistant" in md

    def test_preserves_text_content(self) -> None:
        md = render_messages_to_markdown([user_message("hello world")])
        assert "hello world" in md

    def test_renders_tool_use_blocks(self) -> None:
        md = render_messages_to_markdown([tool_use_message("Bash", {"command": "ls -la"})])
        assert "### Tool Use: Bash" in md
        assert "```json" in md
        assert "ls -la" in md

    def test_uses_longer_fence_when_tool_input_contains_backticks(self) -> None:
        md = render_messages_to_markdown([tool_use_message("Bash", {"command": 'printf "```"'})])
        assert "````json" in md
        assert 'printf \\"```\\"' in md

    def test_renders_tool_result_blocks(self) -> None:
        md = render_messages_to_markdown([tool_result_message("file1.txt\nfile2.txt")])
        assert "## Tool Result" in md
        assert "file1.txt" in md
        # No duplicate sub-heading since the message heading already says it.
        assert "### Tool Result" not in md

    def test_uses_longer_fence_when_tool_output_contains_backticks(self) -> None:
        md = render_messages_to_markdown([tool_result_message("before\n```\ninside\n```\nafter")])
        assert "````text" in md
        assert "before\n```\ninside\n```\nafter" in md

    def test_marks_errored_tool_results_from_runtime_is_error_field(self) -> None:
        md = render_messages_to_markdown([tool_result_message("failure output", True)])
        assert "failure output" in md
        assert "*(Error)*" in md

    def test_renders_tool_result_from_runtime_shape(self) -> None:
        runtime_tool_result = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "output text"}],
            },
        }
        md = render_messages_to_markdown([runtime_tool_result])
        assert "## Tool Result" in md
        assert "output text" in md
        assert "## User" not in md
        assert "### Tool Result" not in md

    def test_does_not_label_mixed_human_text_and_tool_result_as_tool_result(self) -> None:
        mixed_message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu-1", "content": "tool output"},
                    {"type": "text", "text": "human follow-up"},
                ],
            },
        }
        md = render_messages_to_markdown([mixed_message])
        assert "## User" in md
        assert "### Tool Result" in md
        assert "human follow-up" in md

    def test_treats_system_reminder_siblings_as_tool_result_metadata(self) -> None:
        runtime_tool_result = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu-1", "content": "tool output"},
                    {"type": "text", "text": "<system-reminder>note</system-reminder>"},
                ],
            },
        }
        md = render_messages_to_markdown([runtime_tool_result])
        assert "## Tool Result" in md
        assert "## User" not in md
        assert "<system-reminder>" not in md

    def test_handles_empty_messages_array(self) -> None:
        md = render_messages_to_markdown([])
        assert "# Conversation Export" in md
        assert "## User" not in md

    def test_handles_null_messages_gracefully(self) -> None:
        md = render_messages_to_markdown([None, None])
        assert "# Conversation Export" in md

    def test_renders_image_placeholder(self) -> None:
        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png"}}],
            },
        }
        md = render_messages_to_markdown([msg])
        assert "[Image attachment]" in md

    def test_skips_progress_messages(self) -> None:
        progress_msg = {
            "type": "progress",
            "message": {"role": "user", "content": [{"type": "text", "text": "Loading..."}]},
        }
        md = render_messages_to_markdown([progress_msg, user_message("hello")])
        assert "## Progress" not in md
        assert "## User" in md

    def test_skips_attachment_messages(self) -> None:
        att_msg = {"type": "attachment", "attachment": {"type": "file", "name": "test.txt"}}
        md = render_messages_to_markdown([att_msg, user_message("hello")])
        assert "## Attachment" not in md
        assert "## User" in md

    def test_skips_system_api_metrics_messages(self) -> None:
        metrics_msg = {
            "type": "system",
            "subtype": "api_metrics",
            "message": {"role": "system", "content": []},
        }
        md = render_messages_to_markdown([metrics_msg, user_message("hello")])
        assert "## System" not in md
        assert "## User" in md

    def test_skips_messages_with_empty_content(self) -> None:
        empty_msg = {"type": "system", "message": {"role": "system", "content": []}}
        md = render_messages_to_markdown([empty_msg, user_message("hello")])
        assert "## System" not in md
        assert "## User" in md

    def test_skips_thinking_blocks(self) -> None:
        msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "Let me think about this..."}],
            },
        }
        md = render_messages_to_markdown([msg])
        assert "Let me think about this..." not in md
        assert "## Assistant" not in md

    def test_skips_redacted_thinking_blocks(self) -> None:
        msg = {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "redacted_thinking"}]},
        }
        md = render_messages_to_markdown([msg])
        assert "redacted_thinking" not in md
        assert "## Assistant" not in md

    def test_renders_top_level_system_message_content(self) -> None:
        md = render_messages_to_markdown(
            [{"type": "system", "subtype": "local_command", "content": "local command output"}]
        )
        assert "## System" in md
        assert "local command output" in md

    def test_renders_unknown_content_block_payload(self) -> None:
        md = render_messages_to_markdown(
            [
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "server_tool_use", "id": "srv-1", "name": "web_search"}],
                    },
                }
            ]
        )
        assert "*[server_tool_use content block]*" in md
        assert "```json" in md
        assert '"id": "srv-1"' in md
        assert '"name": "web_search"' in md

    def test_handles_malformed_numeric_message_types(self) -> None:
        md = render_messages_to_markdown(
            [{"type": 42, "message": {"content": [{"type": "text", "text": "numeric type"}]}}]
        )
        assert "## 42" in md
        assert "numeric type" in md

    def test_skips_internal_meta_and_breadcrumb_messages(self) -> None:
        # Adaptation: <command-name> -> <task-notification> (Python's only live
        # internal text tag). isMeta + internal-text-only messages are dropped.
        md = render_messages_to_markdown(
            [
                {
                    "type": "user",
                    "isMeta": True,
                    "message": {"role": "user", "content": [{"type": "text", "text": "internal caveat"}]},
                },
                {
                    "type": "user",
                    "message": {"role": "user", "content": "<task-notification>note</task-notification>"},
                },
                {
                    "type": "user",
                    "message": {"role": "user", "content": "<system-reminder>hidden</system-reminder>"},
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "<system-reminder>hidden array</system-reminder>"}],
                    },
                },
                user_message("visible user text"),
            ]
        )
        assert "internal caveat" not in md
        assert "<task-notification>" not in md
        assert "<system-reminder>" not in md
        assert "visible user text" in md

    def test_filters_internal_text_blocks_preserving_mixed_real_content(self) -> None:
        md = render_messages_to_markdown(
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<task-notification>note</task-notification>"},
                            {"type": "text", "text": "real user content"},
                        ],
                    },
                }
            ]
        )
        assert "## User" in md
        assert "real user content" in md
        assert "<task-notification>" not in md

    def test_does_not_drop_visible_text_containing_internal_tag_wrappers(self) -> None:
        md = render_messages_to_markdown(
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "visible before <task-notification>x</task-notification> visible after",
                            }
                        ],
                    },
                }
            ]
        )
        assert "## User" in md
        assert "visible before  visible after" in md
        assert "<task-notification>" not in md

    def test_strips_system_reminders_embedded_in_visible_text(self) -> None:
        md = render_messages_to_markdown(
            [user_message("visible before <system-reminder>hidden</system-reminder> visible after")]
        )
        assert "visible before  visible after" in md
        assert "<system-reminder>" not in md
        assert "hidden" not in md

    def test_renders_fallback_for_non_array_unknown_content(self) -> None:
        md = render_messages_to_markdown([{"type": "custom", "content": {"value": 42}}])
        assert "## Custom" in md
        assert "*[unknown content]*" in md
        assert '"value": 42' in md

    def test_renders_fallback_for_primitive_array_entries(self) -> None:
        md = render_messages_to_markdown(
            [{"type": "assistant", "message": {"role": "assistant", "content": ["primitive content"]}}]
        )
        assert "## Assistant" in md
        assert "*[unknown content]*" in md
        assert "primitive content" in md


# --------------------------------------------------------------------------- #
# renderMessagesToJSON
# --------------------------------------------------------------------------- #


class TestRenderMessagesToJSON:
    def test_produces_valid_json(self) -> None:
        json.loads(render_messages_to_json([user_message("hello")]))  # no raise

    def test_includes_version_1(self) -> None:
        assert _parse([])["version"] == 1

    def test_includes_format_json(self) -> None:
        assert _parse([])["format"] == "json"

    def test_includes_exported_at_as_iso_string(self) -> None:
        assert re.match(r"^\d{4}-\d{2}-\d{2}T", _parse([])["exportedAt"])

    def test_includes_message_count(self) -> None:
        parsed = _parse([user_message("a"), assistant_message("b")])
        assert parsed["messageCount"] == 2

    def test_preserves_message_order(self) -> None:
        parsed = _parse(
            [user_message("first"), assistant_message("second"), user_message("third")]
        )
        assert parsed["messages"][0]["content"][0]["text"] == "first"
        assert parsed["messages"][1]["content"][0]["text"] == "second"
        assert parsed["messages"][2]["content"][0]["text"] == "third"

    def test_includes_message_type_and_role(self) -> None:
        parsed = _parse([user_message("hello")])
        assert parsed["messages"][0]["type"] == "user"
        assert parsed["messages"][0]["role"] == "user"

    def test_handles_tool_use_content_blocks(self) -> None:
        parsed = _parse([tool_use_message("Read", {"file_path": "/test.ts"})])
        content = parsed["messages"][0]["content"][0]
        assert content["type"] == "tool_use"
        assert content["name"] == "Read"
        assert content["input"]["file_path"] == "/test.ts"

    def test_handles_tool_result_content_blocks(self) -> None:
        parsed = _parse([tool_result_message("result text", True)])
        assert parsed["messages"][0]["role"] == "tool"
        content = parsed["messages"][0]["content"][0]
        assert content["type"] == "tool_result"
        assert content["content"] == "result text"
        assert content["isError"] is True

    def test_exports_runtime_tool_result_as_semantic_tool_message(self) -> None:
        runtime_tool_result = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "output"}],
            },
        }
        parsed = _parse([runtime_tool_result])
        msg = parsed["messages"][0]
        assert msg["type"] == "tool"
        assert msg["role"] == "tool"
        assert "internalType" not in msg
        assert "rawType" not in msg
        assert msg["content"][0]["type"] == "tool_result"

    def test_keeps_mixed_human_text_and_tool_result_as_user_message(self) -> None:
        mixed_message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu-1", "content": "tool output"},
                    {"type": "text", "text": "human follow-up"},
                ],
            },
        }
        parsed = _parse([mixed_message])
        msg = parsed["messages"][0]
        assert msg["type"] == "user"
        assert msg["role"] == "user"
        assert "internalType" not in msg
        assert [block["type"] for block in msg["content"]] == ["tool_result", "text"]

    def test_keeps_mixed_non_text_and_tool_result_as_user_message(self) -> None:
        mixed_message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu-1", "content": "tool output"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
                ],
            },
        }
        parsed = _parse([mixed_message])
        msg = parsed["messages"][0]
        assert msg["type"] == "user"
        assert msg["role"] == "user"
        assert [block["type"] for block in msg["content"]] == ["tool_result", "image"]

    def test_exports_system_reminder_siblings_as_semantic_tool_messages(self) -> None:
        runtime_tool_result = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu-1", "content": "tool output"},
                    {"type": "text", "text": "<system-reminder>note</system-reminder>"},
                ],
            },
        }
        parsed = _parse([runtime_tool_result])
        msg = parsed["messages"][0]
        assert msg["type"] == "tool"
        assert msg["role"] == "tool"
        assert "internalType" not in msg
        assert "rawType" not in msg
        assert len(msg["content"]) == 1

    def test_safely_handles_non_serializable_content(self) -> None:
        cyclic: Dict[str, Any] = {"a": 1}
        cyclic["self"] = cyclic
        msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Cyclic", "input": cyclic}],
            },
        }
        parsed = _parse([msg])
        assert parsed["messages"][0]["content"][0]["input"] == {"a": 1, "self": "[Circular]"}

    def test_safely_handles_throwing_getters_in_json_content(self) -> None:
        # Python analog of the TS throwing-getter fixture: a Mapping whose item
        # access raises. safe_json_value reaches the per-key guard -> sentinel.
        class _ThrowingGetterMapping(dict):
            def __getitem__(self, key: Any) -> Any:
                if key == "boom":
                    raise RuntimeError("getter exploded")
                return super().__getitem__(key)

        hostile = _ThrowingGetterMapping(boom=None)
        msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Hostile", "input": hostile}],
            },
        }
        parsed = _parse([msg])
        assert parsed["messages"][0]["content"][0]["input"] == {"boom": "[Unserializable]"}

    def test_exports_unknown_message_types_with_stable_public_type_and_raw_type(self) -> None:
        parsed = _parse([{"type": "custom"}])
        msg = parsed["messages"][0]
        assert msg["type"] == "unknown"
        assert msg["role"] == "unknown"
        assert msg["rawType"] == "custom"

    def test_does_not_emit_empty_raw_type_for_messages_without_a_type(self) -> None:
        parsed = _parse([{}])
        msg = parsed["messages"][0]
        assert msg["type"] == "unknown"
        assert msg["role"] == "unknown"
        assert "rawType" not in msg

    def test_normalizes_camel_case_tool_result_ids(self) -> None:
        parsed = _parse(
            [
                {
                    "type": "tool",
                    "message": {
                        "role": "tool",
                        "content": [
                            {"type": "tool_result", "toolUseId": "camel-id", "content": "ok", "isError": False}
                        ],
                    },
                }
            ]
        )
        content = parsed["messages"][0]["content"][0]
        assert content["toolUseId"] == "camel-id"
        assert content["isError"] is False

    def test_preserves_timestamp_when_present(self) -> None:
        parsed = _parse([user_message("hello")])
        assert parsed["messages"][0]["timestamp"] == "2026-05-13T12:00:00Z"

    def test_uses_2_space_indentation(self) -> None:
        rendered = render_messages_to_json([user_message("hello")])
        assert '  "version"' in rendered

    def test_filters_out_progress_messages(self) -> None:
        progress_msg = {
            "type": "progress",
            "message": {"role": "user", "content": [{"type": "text", "text": "Loading..."}]},
        }
        parsed = _parse([progress_msg, user_message("hello")])
        assert parsed["messageCount"] == 1
        assert parsed["messages"][0]["type"] == "user"

    def test_filters_out_attachment_messages(self) -> None:
        att_msg = {"type": "attachment", "attachment": {"type": "file"}}
        parsed = _parse([att_msg, user_message("hello")])
        assert parsed["messageCount"] == 1

    def test_skips_thinking_content_blocks(self) -> None:
        msg = {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "inner thoughts"}]},
        }
        assert _parse([msg])["messageCount"] == 0

    def test_skips_redacted_thinking_content_blocks(self) -> None:
        msg = {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "redacted_thinking"}]},
        }
        assert _parse([msg])["messageCount"] == 0

    def test_preserves_top_level_system_message_content(self) -> None:
        parsed = _parse(
            [
                {
                    "type": "system",
                    "subtype": "local_command",
                    "content": "local command output",
                    "timestamp": "2026-05-13T12:00:04Z",
                }
            ]
        )
        assert parsed["messageCount"] == 1
        msg = parsed["messages"][0]
        assert msg["type"] == "system"
        assert msg["subtype"] == "local_command"
        assert msg["content"][0] == {"type": "text", "text": "local command output"}

    def test_strips_system_reminders_embedded_in_visible_json_text(self) -> None:
        parsed = _parse(
            [user_message("visible before <system-reminder>hidden</system-reminder> visible after")]
        )
        assert parsed["messages"][0]["content"][0]["text"] == "visible before  visible after"

    def test_strips_system_reminders_nested_inside_tool_result_content(self) -> None:
        parsed = _parse(
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tu-1",
                                "content": [
                                    {"type": "text", "text": "visible <system-reminder>hidden</system-reminder>"}
                                ],
                            }
                        ],
                    },
                }
            ]
        )
        assert parsed["messages"][0]["content"][0]["content"][0]["text"] == "visible"
        serialized = json.dumps(parsed)
        assert "system-reminder" not in serialized
        assert "hidden" not in serialized

    def test_preserves_source_index_when_internal_messages_filtered(self) -> None:
        parsed = _parse(
            [
                {"type": "progress", "message": {"role": "user", "content": [{"type": "text", "text": "loading"}]}},
                user_message("visible"),
            ]
        )
        assert parsed["messages"][0]["index"] == 0
        assert parsed["messages"][0]["sourceIndex"] == 1

    def test_skips_synthetic_missing_tool_result_placeholders(self) -> None:
        parsed = _parse(
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "missing",
                                "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                                "is_error": True,
                            }
                        ],
                    },
                },
                user_message("visible"),
            ]
        )
        assert parsed["messageCount"] == 1
        assert parsed["messages"][0]["content"][0]["text"] == "visible"

    def test_skips_synthetic_missing_tool_result_placeholders_with_internal_siblings(self) -> None:
        parsed = _parse(
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "missing",
                                "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                                "is_error": True,
                            },
                            {"type": "text", "text": "<system-reminder>hidden metadata</system-reminder>"},
                        ],
                    },
                },
                user_message("visible"),
            ]
        )
        assert parsed["messageCount"] == 1
        assert parsed["messages"][0]["content"][0]["text"] == "visible"

    def test_skips_synthetic_first_text_bracketed_literals(self) -> None:
        # Pins the corrected ``_SYNTHETIC_FIRST_TEXTS`` contract: TS
        # ``isSyntheticContent`` filters on five *bracketed display literals*.
        # Three of them have no Python constant but must still be filtered so the
        # synthetic-message filter stays byte-identical to TS.
        for literal in ("[Request cancelled]", "[Tool use rejected]", "[No response requested]"):
            parsed = _parse(
                [
                    {
                        "type": "user",
                        "message": {"role": "user", "content": [{"type": "text", "text": literal}]},
                    },
                    user_message("visible"),
                ]
            )
            assert parsed["messageCount"] == 1, literal
            assert parsed["messages"][0]["content"][0]["text"] == "visible"

    def test_does_not_skip_semantic_cancel_message_sentence(self) -> None:
        # The flip side of the contract: Python's ``CANCEL_MESSAGE`` is a full
        # sentence, NOT the bracketed literal TS keys on, so it must remain
        # exportable. Filtering on it instead would over-filter relative to TS.
        parsed = _parse(
            [
                {
                    "type": "user",
                    "message": {"role": "user", "content": [{"type": "text", "text": CANCEL_MESSAGE}]},
                },
                user_message("visible"),
            ]
        )
        assert parsed["messageCount"] == 2
        assert parsed["messages"][0]["content"][0]["text"] == CANCEL_MESSAGE

    def test_preserves_unknown_content_block_type_while_sanitizing_value(self) -> None:
        parsed = _parse(
            [
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "server_tool_use", "id": "srv-1", "name": "web_search"}],
                    },
                }
            ]
        )
        content = parsed["messages"][0]["content"][0]
        assert content["type"] == "server_tool_use"
        assert content["value"] == {"type": "server_tool_use", "id": "srv-1", "name": "web_search"}

    def test_skips_internal_meta_and_breadcrumb_messages(self) -> None:
        # Adaptation: <command-name> -> <task-notification> (see module docstring).
        parsed = _parse(
            [
                {
                    "type": "user",
                    "isMeta": True,
                    "message": {"role": "user", "content": [{"type": "text", "text": "internal caveat"}]},
                },
                {
                    "type": "user",
                    "message": {"role": "user", "content": "<task-notification>note</task-notification>"},
                },
                {
                    "type": "user",
                    "message": {"role": "user", "content": "<system-reminder>hidden</system-reminder>"},
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "<system-reminder>hidden array</system-reminder>"}],
                    },
                },
                user_message("visible user text"),
            ]
        )
        assert parsed["messageCount"] == 1
        assert parsed["messages"][0]["content"][0]["text"] == "visible user text"

    def test_filters_internal_text_blocks_preserving_mixed_real_content(self) -> None:
        parsed = _parse(
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<task-notification>note</task-notification>"},
                            {"type": "text", "text": "real user content"},
                        ],
                    },
                }
            ]
        )
        assert parsed["messageCount"] == 1
        assert parsed["messages"][0]["content"] == [{"type": "text", "text": "real user content"}]

    def test_does_not_drop_visible_text_containing_internal_tag_wrappers(self) -> None:
        parsed = _parse(
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "visible before <task-notification>x</task-notification> visible after",
                            }
                        ],
                    },
                }
            ]
        )
        assert parsed["messageCount"] == 1
        assert parsed["messages"][0]["type"] == "user"
        assert parsed["messages"][0]["content"][0]["text"] == "visible before  visible after"


# --------------------------------------------------------------------------- #
# render_messages_to_plain_text  (no TS reference — Python-only transcript)
# --------------------------------------------------------------------------- #


class TestRenderMessagesToPlainText:
    def test_includes_header(self) -> None:
        text = render_messages_to_plain_text([user_message("hello")])
        assert "Conversation Export" in text
        assert "Format: Text" in text

    def test_includes_exported_timestamp(self) -> None:
        text = render_messages_to_plain_text([])
        assert re.search(r"Exported: \d{4}-\d{2}-\d{2}T", text)

    def test_renders_plain_user_and_assistant_headings(self) -> None:
        text = render_messages_to_plain_text([user_message("hello"), assistant_message("world")])
        lines = text.splitlines()
        assert "User" in lines
        assert "Assistant" in lines
        # No markdown heading syntax in the plain-text format.
        assert "## User" not in text

    def test_preserves_text_content(self) -> None:
        text = render_messages_to_plain_text([user_message("hello world")])
        assert "hello world" in text

    def test_renders_tool_use_blocks(self) -> None:
        text = render_messages_to_plain_text([tool_use_message("Bash", {"command": "ls -la"})])
        assert "Tool Use: Bash" in text
        assert "ls -la" in text

    def test_renders_tool_result_blocks(self) -> None:
        text = render_messages_to_plain_text([tool_result_message("file1.txt\nfile2.txt")])
        assert "Tool Result" in text.splitlines()
        assert "file1.txt" in text

    def test_marks_errored_tool_results(self) -> None:
        text = render_messages_to_plain_text([tool_result_message("failure output", True)])
        assert "failure output" in text
        assert "(Error)" in text

    def test_renders_image_placeholder(self) -> None:
        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png"}}],
            },
        }
        text = render_messages_to_plain_text([msg])
        assert "[Image attachment]" in text

    def test_strips_system_reminders_in_visible_text(self) -> None:
        text = render_messages_to_plain_text(
            [user_message("visible before <system-reminder>hidden</system-reminder> visible after")]
        )
        assert "visible before  visible after" in text
        assert "<system-reminder>" not in text
        assert "hidden" not in text

    def test_skips_progress_messages(self) -> None:
        progress_msg = {
            "type": "progress",
            "message": {"role": "user", "content": [{"type": "text", "text": "Loading..."}]},
        }
        text = render_messages_to_plain_text([progress_msg, user_message("hello")])
        assert "Loading..." not in text
        assert "User" in text.splitlines()

    def test_skips_thinking_blocks(self) -> None:
        msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "Let me think about this..."}],
            },
        }
        text = render_messages_to_plain_text([msg])
        assert "Let me think about this..." not in text
        assert "Assistant" not in text.splitlines()

    def test_skips_synthetic_first_text_messages(self) -> None:
        synthetic = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": INTERRUPT_MESSAGE}]},
        }
        text = render_messages_to_plain_text([synthetic, user_message("visible")])
        assert INTERRUPT_MESSAGE not in text
        assert "visible" in text

    def test_skips_synthetic_tool_result_placeholder_messages(self) -> None:
        synthetic = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "missing",
                        "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                        "is_error": True,
                    }
                ],
            },
        }
        text = render_messages_to_plain_text([synthetic, user_message("visible")])
        assert SYNTHETIC_TOOL_RESULT_PLACEHOLDER not in text
        assert "visible" in text

    def test_empty_messages_has_header_only(self) -> None:
        text = render_messages_to_plain_text([])
        assert "Conversation Export" in text
        assert "User" not in text.splitlines()

    def test_dispatcher_routes_each_format(self) -> None:
        from src.utils.export_renderer import render_messages_for_export

        msgs = [user_message("hello")]
        assert "Format: Text" in render_messages_for_export(msgs, format="text")
        assert "Format: Markdown" in render_messages_for_export(msgs, format="markdown")
        assert '"format": "json"' in render_messages_for_export(msgs, format="json")

    def test_dispatcher_rejects_unknown_format(self) -> None:
        import pytest

        from src.utils.export_renderer import render_messages_for_export

        with pytest.raises(ValueError):
            render_messages_for_export([], format="xml")  # type: ignore[arg-type]

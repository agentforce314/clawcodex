import pytest

from src.services.compact.prompt import (
    BASE_COMPACT_PROMPT,
    NO_TOOLS_PREAMBLE,
    NO_TOOLS_TRAILER,
    PARTIAL_COMPACT_PROMPT,
    PARTIAL_COMPACT_UP_TO_PROMPT,
    format_compact_summary,
    get_compact_prompt,
    get_compact_user_summary_message,
    get_partial_compact_prompt,
)


class TestBaseCompactPrompt:
    def test_contains_required_sections(self):
        assert "Primary Request" in BASE_COMPACT_PROMPT
        assert "Files and Code Sections" in BASE_COMPACT_PROMPT
        assert "Errors and fixes" in BASE_COMPACT_PROMPT
        assert "Pending Tasks" in BASE_COMPACT_PROMPT
        assert "Current Work" in BASE_COMPACT_PROMPT
        assert "Optional Next Step" in BASE_COMPACT_PROMPT

    def test_contains_analysis_instruction(self):
        assert "<analysis>" in BASE_COMPACT_PROMPT
        assert "<summary>" in BASE_COMPACT_PROMPT

    def test_contains_example(self):
        assert "<example>" in BASE_COMPACT_PROMPT

    def test_nine_sections(self):
        for i in range(1, 10):
            assert f"{i}." in BASE_COMPACT_PROMPT


class TestGetCompactPrompt:
    def test_default(self):
        prompt = get_compact_prompt()
        assert len(prompt) > 100
        assert NO_TOOLS_PREAMBLE in prompt
        assert "REMINDER: Do NOT call any tools" in prompt  # NO_TOOLS_TRAILER

    def test_with_custom_instructions(self):
        prompt = get_compact_prompt("Focus on TypeScript files")
        assert "Focus on TypeScript files" in prompt
        assert "Additional Instructions" in prompt


class TestGetPartialCompactPrompt:
    def test_earlier_uses_up_to_prompt(self):
        prompt = get_partial_compact_prompt("earlier")
        assert "continuing session" in prompt
        assert "Context for Continuing Work" in prompt

    def test_up_to_uses_up_to_prompt(self):
        prompt = get_partial_compact_prompt("up_to")
        assert "continuing session" in prompt

    def test_later_uses_partial_prompt(self):
        prompt = get_partial_compact_prompt("later")
        assert "RECENT portion" in prompt

    def test_from_uses_partial_prompt(self):
        prompt = get_partial_compact_prompt("from")
        assert "RECENT portion" in prompt

    def test_with_custom_instructions(self):
        prompt = get_partial_compact_prompt("earlier", "Preserve test results")
        assert "Preserve test results" in prompt

    def test_contains_trailer(self):
        prompt = get_partial_compact_prompt("later")
        assert "REMINDER: Do NOT call any tools" in prompt


class TestFormatCompactSummary:
    def test_strip_whitespace(self):
        result = format_compact_summary("  hello  ")
        assert result == "hello"

    def test_normalize_blank_lines(self):
        result = format_compact_summary("a\n\n\n\n\nb")
        assert result == "a\n\nb"

    def test_strips_analysis_block(self):
        raw = "<analysis>some thinking here</analysis>\n<summary>the real summary</summary>"
        result = format_compact_summary(raw)
        assert "some thinking here" not in result
        assert "the real summary" in result
        assert "<analysis>" not in result

    def test_formats_summary_tags(self):
        raw = "<summary>My Summary Content</summary>"
        result = format_compact_summary(raw)
        assert "Summary:" in result
        assert "My Summary Content" in result
        assert "<summary>" not in result

    def test_handles_no_xml_tags(self):
        raw = "Just a plain text summary"
        result = format_compact_summary(raw)
        assert result == "Just a plain text summary"

    def test_add_files_modified_section(self):
        result = format_compact_summary(
            "Summary text",
            files_modified=["src/main.py", "tests/test_main.py"],
        )
        assert "## Files Modified" in result
        assert "src/main.py" in result

    def test_no_duplicate_files_section(self):
        result = format_compact_summary(
            "Summary\n## Files Modified\n- existing.py",
            files_modified=["new.py"],
        )
        assert result.count("## Files Modified") == 1

    def test_add_tools_used_section(self):
        result = format_compact_summary(
            "Summary",
            tools_used=["Bash", "Read", "Bash", "Edit"],
        )
        assert "## Tools Used" in result
        assert "Bash (x2)" in result

    def test_no_extras_by_default(self):
        result = format_compact_summary("Simple summary")
        assert "## Files Modified" not in result
        assert "## Tools Used" not in result


class TestGetCompactUserSummaryMessage:
    def test_default(self):
        msg = get_compact_user_summary_message("My summary")
        assert "continued from a previous conversation" in msg
        assert "My summary" in msg

    def test_suppress_follow_up(self):
        msg = get_compact_user_summary_message("Summary", suppress_follow_up=True)
        assert "Continue the conversation" in msg
        assert "without asking" in msg

    def test_transcript_path(self):
        msg = get_compact_user_summary_message(
            "Summary", transcript_path="/tmp/transcript.md"
        )
        assert "/tmp/transcript.md" in msg
        assert "full transcript" in msg

    def test_recent_messages_preserved(self):
        msg = get_compact_user_summary_message(
            "Summary", recent_messages_preserved=True
        )
        assert "Recent messages are preserved" in msg

import pytest

from src.services.compact.reactive_compact import (
    _drop_oldest_messages,
    build_post_compact_messages,
    clear_withheld_errors,
    get_withheld_errors,
    is_prompt_too_long_error,
    withhold_error,
)
from src.services.compact.session_memory_compact import (
    SessionMemory,
    try_session_memory_compaction,
)
from src.services.compact.prompt import (
    format_compact_summary,
    get_compact_prompt,
)
from src.types.messages import AssistantMessage, UserMessage


class TestCompactIntegration:
    def setup_method(self):
        clear_withheld_errors()

    def teardown_method(self):
        clear_withheld_errors()

    def test_reactive_compact_flow(self):
        error = Exception("prompt_too_long: context too large")
        assert is_prompt_too_long_error(error)

        messages = [
            UserMessage(role="user", content=f"msg {i}")
            if i % 2 == 0
            else AssistantMessage(role="assistant", content=f"resp {i}")
            for i in range(20)
        ]

        dropped = _drop_oldest_messages(messages, 0.5)
        assert len(dropped) < len(messages)

        summary = format_compact_summary(
            "The user asked about Python features and I helped with several tasks.",
            files_modified=["src/main.py", "tests/test_main.py"],
            tools_used=["Bash", "Read", "Edit", "Bash"],
        )
        assert "## Files Modified" in summary
        assert "## Tools Used" in summary
        assert "Bash (x2)" in summary

        post_messages = build_post_compact_messages(summary, dropped[-4:])
        assert len(post_messages) >= 1
        assert post_messages[0]["role"] == "user"

    def test_session_memory_with_compaction(self):
        messages = [
            UserMessage(role="user", content=f"question {i}")
            if i % 2 == 0
            else AssistantMessage(role="assistant", content=f"answer {i}")
            for i in range(10)
        ]

        to_summarize, to_keep = try_session_memory_compaction(messages, 4)
        assert len(to_keep) >= 4
        assert len(to_summarize) + len(to_keep) == 10

        mem = SessionMemory()
        mem.add("User is working on Python CLI")
        mem.add("Project uses pytest for testing")
        mem.add("Main file is src/main.py")
        assert mem.count == 3

        formatted = mem.format_memory()
        assert "## Session Memory" in formatted
        assert "pytest" in formatted

    def test_withhold_and_recover(self):
        err = Exception("prompt_too_long")
        withhold_error(err)
        errors = get_withheld_errors()
        assert len(errors) == 1
        clear_withheld_errors()
        assert get_withheld_errors() == []

    def test_compact_prompt_generation(self):
        prompt = get_compact_prompt()
        assert len(prompt) > 100
        assert "Primary Request" in prompt

        prompt_custom = get_compact_prompt("Focus on TypeScript")
        assert "Focus on TypeScript" in prompt_custom

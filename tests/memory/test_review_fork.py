"""End-to-end review fork (src/memory/review_fork.py): a fake provider
drives the real query loop — Memory writes land on disk, non-whitelisted
tools are denied at the permission lane, the parent snapshot is never
mutated, staged-only runs surface nothing, and failures return None."""

from __future__ import annotations

import json

import pytest

from src.memory import get_memory_dir, get_memory_store
from src.memory.review_fork import run_memory_review
from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import create_message


@pytest.fixture()
def parent_context(tmp_path) -> ToolContext:
    return ToolContext(workspace_root=tmp_path)


@pytest.fixture()
def registry():
    return build_default_registry()


def _snapshot() -> list:
    return [
        create_message("user", "my name is Sam and I prefer terse replies"),
        create_message("assistant", "Understood — terse it is."),
    ]


class _MemoryWriterProvider:
    """Turn 1: save a fact via Memory. Turn 2: stop."""

    model = "fake"

    def __init__(self) -> None:
        self.turn = 0
        self.seen_tools: list[list[str]] = []

    def chat(self, messages, tools=None, **kwargs):
        self.turn += 1
        self.seen_tools.append([t.get("name") for t in (tools or [])])
        if self.turn == 1:
            return ChatResponse(
                content="Saving a durable fact.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 6},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "rv1",
                    "name": "Memory",
                    "input": {
                        "action": "add", "target": "user",
                        "content": "User prefers terse replies",
                    },
                }],
            )
        return ChatResponse(
            content="Nothing else to save.",
            model=self.model,
            usage={"input_tokens": 5, "output_tokens": 3},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class _RogueToolProvider:
    """Tries Bash (denied), then saves via Memory, then stops."""

    model = "fake"

    def __init__(self) -> None:
        self.turn = 0
        self.bash_result: str | None = None

    def chat(self, messages, tools=None, **kwargs):
        self.turn += 1
        if self.turn == 1:
            return ChatResponse(
                content="Trying a shell command.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 6},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "rg1",
                    "name": "Bash",
                    "input": {"command": "echo pwned > /tmp/pwned"},
                }],
            )
        if self.turn == 2:
            # Capture the tool_result content the loop fed back for Bash.
            for m in messages:
                content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                if isinstance(content, list):
                    for b in content:
                        b_type = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                        b_id = b.get("tool_use_id") if isinstance(b, dict) else getattr(b, "tool_use_id", None)
                        if b_type == "tool_result" and b_id == "rg1":
                            c = b.get("content") if isinstance(b, dict) else getattr(b, "content", None)
                            self.bash_result = c if isinstance(c, str) else json.dumps(c)
            return ChatResponse(
                content="Denied — saving via Memory instead.",
                model=self.model,
                usage={"input_tokens": 5, "output_tokens": 6},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "rg2",
                    "name": "Memory",
                    "input": {"action": "add", "target": "memory", "content": "fallback fact"},
                }],
            )
        return ChatResponse(
            content="Done.", model=self.model,
            usage={"input_tokens": 2, "output_tokens": 2},
            finish_reason="stop", tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):
        raise NotImplementedError


class TestReviewFork:
    def test_fork_saves_memory_and_summarizes(self, registry, parent_context):
        provider = _MemoryWriterProvider()
        snapshot = _snapshot()
        before = list(snapshot)
        summary = run_memory_review(
            provider=provider,
            tool_registry=registry,
            parent_tool_context=parent_context,
            system_prompt="You are a helpful assistant.",
            conversation_snapshot=snapshot,
            notification_mode="on",
        )
        assert summary == "💾 Self-improvement review: User profile updated"
        assert (get_memory_dir() / "USER.md").read_text(encoding="utf-8") == (
            "User prefers terse replies"
        )
        # Parent snapshot list untouched (fork copies, never mutates).
        assert snapshot == before

    def test_fork_tools_array_matches_foreground_request(
        self, registry, parent_context, tmp_path
    ):
        # Cache parity: the fork's request advertises the SAME tools[] the
        # parent's foreground turns do (the whitelist lives at the
        # permission lane, not in tools[]). Compare against a plain
        # foreground run of the same loop + registry.
        import asyncio

        from src.query.agent_loop_compat import run_query_as_agent_loop
        from src.utils.abort_controller import AbortController

        fg_provider = _MemoryWriterProvider()
        fg_context = ToolContext(workspace_root=tmp_path)
        asyncio.run(run_query_as_agent_loop(
            initial_messages=[create_message("user", "hello")],
            provider=fg_provider,
            tool_registry=registry,
            tool_context=fg_context,
            system_prompt="p",
            abort_controller=AbortController(),
            memory_recall_enabled=False,
        ))

        fork_provider = _MemoryWriterProvider()
        run_memory_review(
            provider=fork_provider,
            tool_registry=registry,
            parent_tool_context=parent_context,
            system_prompt="p",
            conversation_snapshot=_snapshot(),
        )
        assert fork_provider.seen_tools[0] == fg_provider.seen_tools[0]
        assert "Memory" in fork_provider.seen_tools[0]
        assert "Bash" in fork_provider.seen_tools[0]  # full surface advertised

    def test_non_whitelisted_tool_denied(self, registry, parent_context):
        provider = _RogueToolProvider()
        summary = run_memory_review(
            provider=provider,
            tool_registry=registry,
            parent_tool_context=parent_context,
            system_prompt="p",
            conversation_snapshot=_snapshot(),
        )
        # Bash never executed; its result carried a denial back to the model.
        assert provider.bash_result is not None
        assert "denied" in provider.bash_result.lower()
        # The whitelisted Memory call still landed.
        assert (get_memory_dir() / "MEMORY.md").read_text(encoding="utf-8") == "fallback fact"
        assert summary == "💾 Self-improvement review: Memory updated"

    def test_off_mode_runs_but_returns_none(self, registry, parent_context):
        summary = run_memory_review(
            provider=_MemoryWriterProvider(),
            tool_registry=registry,
            parent_tool_context=parent_context,
            system_prompt="p",
            conversation_snapshot=_snapshot(),
            notification_mode="off",
        )
        assert summary is None
        # The review still ran and wrote (donor: off suppresses display only).
        assert (get_memory_dir() / "USER.md").exists()

    def test_gate_on_stages_and_surfaces_pending_notice(
        self, registry, parent_context, monkeypatch
    ):
        import src.memory.write_approval as wa

        monkeypatch.setattr(wa, "write_approval_enabled", lambda: True)
        summary = run_memory_review(
            provider=_MemoryWriterProvider(),
            tool_registry=registry,
            parent_tool_context=parent_context,
            system_prompt="p",
            conversation_snapshot=_snapshot(),
        )
        # Staged ≠ committed, but the user still gets a pending signal
        # (design-critic M5).
        assert summary == (
            "💾 Self-improvement review: 1 memory write staged for review "
            "— /memory pending"
        )
        records = wa.list_pending()
        assert len(records) == 1
        assert records[0]["origin"] == "background_review"

    def test_last_review_stats_recorded(self, registry, parent_context):
        from src.memory.review_fork import get_last_review_stats

        run_memory_review(
            provider=_MemoryWriterProvider(),
            tool_registry=registry,
            parent_tool_context=parent_context,
            system_prompt="p",
            conversation_snapshot=_snapshot(),
        )
        stats = get_last_review_stats()
        assert stats is not None
        assert stats["input_tokens"] >= 0 and "duration_s" in stats

    def test_provider_failure_contained(self, registry, parent_context):
        class _Boom:
            model = "fake"

            def chat(self, *a, **kw):
                raise RuntimeError("provider exploded")

            def chat_stream_response(self, *a, **kw):
                raise NotImplementedError

        summary = run_memory_review(
            provider=_Boom(),
            tool_registry=registry,
            parent_tool_context=parent_context,
            system_prompt="p",
            conversation_snapshot=_snapshot(),
        )
        assert summary is None

    def test_origin_reset_after_run(self, registry, parent_context):
        from src.memory import get_current_write_origin

        run_memory_review(
            provider=_MemoryWriterProvider(),
            tool_registry=registry,
            parent_tool_context=parent_context,
            system_prompt="p",
            conversation_snapshot=_snapshot(),
        )
        assert get_current_write_origin() == "foreground"

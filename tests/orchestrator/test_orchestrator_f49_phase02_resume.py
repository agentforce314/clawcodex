"""F-49 Phase 0.2 validation: top-level ``clawcodex --resume <run_id>``
must work for orchestrator runs.

The orchestrator writes a JSONL transcript to
``~/.clawcodex/sessions/<run_id>/transcript.jsonl`` (via
:class:`SessionStorage` keyed by ``run_id``). The top-level
``--resume`` flow is:

    clawcodex --resume <run_id>
        -> src/cli.py::start_repl(resume_session_id=run_id)
        -> clawcodex_ext/cli/runners.py
        -> Session.resume(run_id)              # bootstrap + cost
        -> resume_session(run_id)              # rehydrate Conversation

This test is the cross-validation: write the transcript the way
:class:`AgentRunner` writes it, then read it back the way
``--resume`` reads it. The two paths share the on-disk format
(JSONL) and the typed ``Message`` schema, so the round-trip
exercises the full integration surface described in
``docs/FEATURE_PLAN.md`` §3.1.11 Phase 0.2.

Patches ``SESSIONS_DIR`` to a tmp dir so the test does not touch
the user's real ``~/.clawcodex/sessions``.
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.services.session_resume import resume_session
from src.services.session_storage import SessionStorage
from src.types.messages import AssistantMessage, UserMessage, message_to_dict


def _write_orchestrator_turn(
    storage: SessionStorage,
    user_text: str,
    assistant_text: str,
    tool_name: str | None = None,
) -> None:
    """Write one user→assistant turn the way AgentRunner does.

    The orchestrator calls ``storage.write_raw(message_to_dict(msg))``
    inside ``_flush_turn_transcript`` — the raw-dict path, NOT
    ``write_message``. We mirror that to keep the test faithful to
    production.
    """
    user_msg = UserMessage(content=[{"type": "text", "text": user_text}])
    storage.write_raw(message_to_dict(user_msg))

    asst_content: list[dict] = [{"type": "text", "text": assistant_text}]
    if tool_name is not None:
        asst_content.append(
            {
                "type": "tool_use",
                "id": "tool-1",
                "name": tool_name,
                "input": {"path": "/work/file.py"},
            },
        )
    asst_msg = AssistantMessage(
        content=asst_content, model="claude-sonnet-4-20250514",
    )
    storage.write_raw(message_to_dict(asst_msg))

    # Synthetic tool_result so the LLM transcript is well-formed.
    if tool_name is not None:
        result_msg = UserMessage(
            content=[{
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "[file contents]",
                "is_error": False,
            }],
        )
        storage.write_raw(message_to_dict(result_msg))

    storage.flush()


class TestOrchestratorResumeRoundTrip(unittest.IsolatedAsyncioTestCase):
    """End-to-end: orchestrator writes → top-level --resume reads."""

    async def test_orchestrator_transcript_is_resumable(self) -> None:
        """Verify the on-disk format the orchestrator produces is
        consumable by the same readers that ``--resume`` invokes.
        """
        with TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp) / "sessions"
            run_id = "run-f49-validation-1"

            # 1. Orchestrator side: write the transcript the way
            #    AgentRunner does in ``_flush_turn_transcript``.
            storage = SessionStorage(
                session_id=run_id, sessions_dir=sessions_dir,
            )
            storage.init_metadata(
                model="claude-sonnet-4-20250514",
                cwd="/work",
                title="orchestrator-F-49-validation",
            )
            _write_orchestrator_turn(
                storage,
                user_text="Fix the bug in the F-49 orchestrator",
                assistant_text="Reading the relevant file first.",
                tool_name="Read",
            )
            _write_orchestrator_turn(
                storage,
                user_text="Continue",
                assistant_text="Applying the fix now.",
            )

            # 2. ``--resume`` side: read the transcript back the way
            #    the top-level CLI does. Patch SESSIONS_DIR so the
            #    reader uses our tmp dir.
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_dir,
            ):
                # Session.resume updates bootstrap state (singleton
                # session_id + cost counters) and returns the loaded
                # Session object. We don't assert on the returned
                # Session here — that's covered by Session tests —
                # only on the JSONL round-trip, which is the F-49
                # contract.
                from src.agent.session import Session
                session = Session.resume(run_id)
                self.assertIsNotNone(session)
                assert session is not None
                self.assertEqual(session.session_id, run_id)
                self.assertEqual(
                    session.model, "claude-sonnet-4-20250514",
                )

                result = resume_session(run_id)
            self.assertTrue(result.success)
            self.assertIsNotNone(result.metadata)
            assert result.metadata is not None
            self.assertEqual(result.metadata.session_id, run_id)
            self.assertEqual(
                result.metadata.model, "claude-sonnet-4-20250514",
            )
            # 2 turns = 2 user + 2 assistant + 1 tool_result user = 5
            self.assertEqual(result.message_count, 5)
            # Rehydrated messages: roles must alternate correctly
            # (user → assistant → user [tool_result] → user → assistant)
            roles = [m.role for m in result.messages]
            self.assertEqual(
                roles,
                [
                    "user", "assistant", "user", "user", "assistant",
                ],
            )
            # Tool-use block must be preserved through the round-trip.
            asst_turns = [
                m for m in result.messages
                if m.role == "assistant"
                and any(
                    getattr(b, "type", None) == "tool_use"
                    for b in getattr(m, "content", [])
                    if not isinstance(b, dict)
                )
            ]
            self.assertEqual(len(asst_turns), 1)
            tool_block = next(
                b for b in asst_turns[0].content
                if getattr(b, "type", None) == "tool_use"
            )
            self.assertEqual(tool_block.name, "Read")
            self.assertEqual(tool_block.id, "tool-1")

    async def test_orchestrator_resume_handles_missing_session(self) -> None:
        """If no transcript exists for ``run_id``, ``--resume`` should
        return a graceful ``ResumeResult(success=False)`` rather
        than raising.
        """
        with TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp) / "sessions"
            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_dir,
            ):
                # ``Session.resume`` returns ``None`` when the
                # session_id file does not exist (see
                # ``Session.load`` at src/agent/session.py:121).
                from src.agent.session import Session
                session = Session.resume("nonexistent-run-id")
                self.assertIsNone(session)

                result = resume_session("nonexistent-run-id")
            self.assertFalse(result.success)
            self.assertEqual(result.message_count, 0)
            self.assertTrue(result.has_warnings)

    async def test_orchestrator_resume_recovers_from_orphan_tool_use(
        self,
    ) -> None:
        """The orchestrator can crash mid-turn, leaving a tool_use
        without a matching tool_result. ``resume_session`` must
        recover by adding a synthetic result (otherwise the LLM
        context is broken on the next round).
        """
        with TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp) / "sessions"
            run_id = "run-orphan"

            storage = SessionStorage(
                session_id=run_id, sessions_dir=sessions_dir,
            )
            storage.init_metadata(
                model="claude-sonnet-4-20250514",
                cwd="/work",
                title="orchestrator-orphan",
            )

            # Simulate: agent started a tool call then crashed
            # before the tool_result was written.
            asst_msg = AssistantMessage(
                content=[
                    {"type": "text", "text": "Reading now..."},
                    {
                        "type": "tool_use",
                        "id": "orphan-1",
                        "name": "Read",
                        "input": {"path": "/x.py"},
                    },
                ],
                model="claude-sonnet-4-20250514",
            )
            storage.write_raw(message_to_dict(asst_msg))
            storage.flush()

            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_dir,
            ):
                result = resume_session(run_id)
            self.assertTrue(result.success)
            # _fix_orphaned_tool_uses adds a synthetic tool_result
            # after the orphan. Original 1 + synthetic 1 = 2.
            self.assertEqual(result.message_count, 2)
            self.assertTrue(result.has_warnings)
            self.assertTrue(
                any(
                    "orphan" in w.lower()
                    for w in result.warnings
                ),
                f"expected orphan warning, got {result.warnings}",
            )


if __name__ == "__main__":
    unittest.main()

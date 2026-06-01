"""F-39 Sub-D: comment command parsing (`/agent retry|follow-up|unblock`).

Covers:
  - parse_agent_command: regex recognition + case-insensitivity + arg tolerance
  - command_to_intent: RETRY/FOLLOWUP/UNBLOCK → Intent mapping
  - merge_intents: BLOCKED > FOLLOWUP > RETRY/NONE
  - TrackerAdapter.fetch_issue_command_intent default returns None
  - RepositoryTrackerAdapter.fetch_issue_command_intent delegates
  - LocalTrackerAdapter.fetch_issue_command_intent scans local comments
  - Orchestrator._post_command_acknowledgement posts comment + sets cursor
  - Orchestrator command source attribution (label vs command)
  - IssueRecord.command_cursor JSON round-trip + back-compat
"""

from __future__ import annotations

import json
import tempfile
import unittest
import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from extensions.orchestrator.config.schema import WorkflowConfig
from extensions.orchestrator.issue import Issue
from extensions.orchestrator.issue_registry import (
    IssueRegistry,
    IssueStatus,
)
from extensions.orchestrator.local_tracker.adapter import LocalTrackerAdapter
from extensions.orchestrator.orchestrator import Orchestrator
from extensions.orchestrator.repo_tracker.adapter import (
    RepositoryTrackerAdapter,
)
from extensions.orchestrator.tracker import (
    Command,
    CommandIntent,
    Comment,
    Intent,
    command_to_intent,
    intent_from_label_set,
    merge_intents,
    parse_agent_command,
)


# ---------------------------------------------------------------------------
# parse_agent_command
# ---------------------------------------------------------------------------


class TestParseAgentCommand(unittest.TestCase):
    def test_retry_recognized(self) -> None:
        self.assertIs(parse_agent_command("/agent retry"), Command.RETRY)
        self.assertIs(
            parse_agent_command("/agent retry the test is broken"),
            Command.RETRY,
        )

    def test_followup_recognized(self) -> None:
        self.assertIs(parse_agent_command("/agent follow-up"), Command.FOLLOWUP)
        self.assertIs(
            parse_agent_command("/agent follow-up please also fix X"),
            Command.FOLLOWUP,
        )

    def test_unblock_recognized(self) -> None:
        self.assertIs(parse_agent_command("/agent unblock"), Command.UNBLOCK)

    def test_case_insensitive(self) -> None:
        self.assertIs(parse_agent_command("/Agent Retry"), Command.RETRY)
        self.assertIs(parse_agent_command("/AGENT FOLLOW-UP"), Command.FOLLOWUP)
        self.assertIs(parse_agent_command("/agent UNBLOCK"), Command.UNBLOCK)

    def test_no_command_returns_none(self) -> None:
        self.assertIsNone(parse_agent_command("hello world"))
        self.assertIsNone(parse_agent_command(""))
        self.assertIsNone(parse_agent_command(None))
        self.assertIsNone(parse_agent_command("thanks for the fix"))

    def test_unrelated_slash_command_returns_none(self) -> None:
        # Only `/agent` is recognized, not `/help` or `/rerun`.
        self.assertIsNone(parse_agent_command("/help"))
        self.assertIsNone(parse_agent_command("/rerun"))

    def test_unrecognized_subcommand_returns_none(self) -> None:
        # `agent run` is not a valid F-39 command.
        self.assertIsNone(parse_agent_command("/agent run"))
        self.assertIsNone(parse_agent_command("/agent dance"))

    def test_must_start_with_slash_agent(self) -> None:
        # The command must begin with `/agent`.
        self.assertIsNone(parse_agent_command("please /agent retry"))
        # Embedded command after a newline IS recognized (multiline).
        self.assertIs(
            parse_agent_command("hello\n/agent retry\nthanks"),
            Command.RETRY,
        )

    def test_returns_first_match(self) -> None:
        # Two commands in the same body → only the first is returned.
        self.assertIs(
            parse_agent_command("/agent retry\n/agent unblock"),
            Command.RETRY,
        )


# ---------------------------------------------------------------------------
# command_to_intent
# ---------------------------------------------------------------------------


class TestCommandToIntent(unittest.TestCase):
    def test_retry_to_retry(self) -> None:
        self.assertIs(command_to_intent(Command.RETRY), Intent.RETRY)

    def test_followup_to_followup(self) -> None:
        self.assertIs(command_to_intent(Command.FOLLOWUP), Intent.FOLLOWUP)

    def test_unblock_to_none(self) -> None:
        self.assertIs(command_to_intent(Command.UNBLOCK), Intent.NONE)


# ---------------------------------------------------------------------------
# merge_intents
# ---------------------------------------------------------------------------


class TestMergeIntents(unittest.TestCase):
    def test_both_none(self) -> None:
        self.assertIs(merge_intents(Intent.NONE, Intent.NONE), Intent.NONE)

    def test_label_only(self) -> None:
        self.assertIs(merge_intents(Intent.RETRY, Intent.NONE), Intent.RETRY)
        self.assertIs(merge_intents(Intent.FOLLOWUP, Intent.NONE), Intent.FOLLOWUP)

    def test_command_only(self) -> None:
        self.assertIs(merge_intents(Intent.NONE, Intent.RETRY), Intent.RETRY)
        self.assertIs(merge_intents(Intent.NONE, Intent.FOLLOWUP), Intent.FOLLOWUP)

    def test_command_beats_label_when_both_retry(self) -> None:
        # RETRY + RETRY = RETRY (no surprise)
        self.assertIs(merge_intents(Intent.RETRY, Intent.RETRY), Intent.RETRY)

    def test_followup_wins_over_retry(self) -> None:
        self.assertIs(merge_intents(Intent.RETRY, Intent.FOLLOWUP), Intent.FOLLOWUP)
        self.assertIs(merge_intents(Intent.FOLLOWUP, Intent.RETRY), Intent.FOLLOWUP)

    def test_blocked_sticky_against_retry(self) -> None:
        self.assertIs(merge_intents(Intent.RETRY, Intent.BLOCKED), Intent.BLOCKED)
        self.assertIs(merge_intents(Intent.BLOCKED, Intent.RETRY), Intent.BLOCKED)

    def test_blocked_sticky_against_followup(self) -> None:
        self.assertIs(merge_intents(Intent.FOLLOWUP, Intent.BLOCKED), Intent.BLOCKED)
        self.assertIs(merge_intents(Intent.BLOCKED, Intent.FOLLOWUP), Intent.BLOCKED)

    def test_both_blocked(self) -> None:
        self.assertIs(merge_intents(Intent.BLOCKED, Intent.BLOCKED), Intent.BLOCKED)

    def test_blocked_label_command_unblock_yields_blocked(self) -> None:
        # UNBLOCK → NONE on the command side, but the BLOCKED label
        # is sticky. The unblock side-effect (status reset) is a
        # separate concern handled by the orchestrator, not by
        # merge_intents.
        self.assertIs(merge_intents(Intent.BLOCKED, Intent.NONE), Intent.BLOCKED)


# ---------------------------------------------------------------------------
# Adapter-level fetch_issue_command_intent
# ---------------------------------------------------------------------------


class TestTrackerAdapterCommandDefault(unittest.IsolatedAsyncioTestCase):
    async def test_default_returns_none(self) -> None:
        class _Stub:
            async def fetch_issue_command_intent(self, issue_id, since):
                return await super().fetch_issue_command_intent(issue_id, since)

        from extensions.orchestrator.tracker import TrackerAdapter

        class _Adapter(TrackerAdapter):
            async def fetch_candidate_issues(self):
                return []

            async def fetch_issue_states_by_ids(self, issue_ids):
                return {}

            async def create_comment(self, issue_id, body):
                return None

            async def update_issue_state(self, issue_id, state):
                return None

        adapter = _Adapter()
        self.assertIsNone(
            await adapter.fetch_issue_command_intent("1", None)
        )
        self.assertIsNone(
            await adapter.fetch_issue_command_intent("1", "cursor-1")
        )


class TestRepositoryTrackerAdapterCommand(unittest.IsolatedAsyncioTestCase):
    async def test_finds_command_in_recent_comments(self) -> None:
        adapter = RepositoryTrackerAdapter(
            platform="github",
            owner="o",
            repo="r",
            api_key="dummy",
        )
        with patch_fetch_new_comments(
            adapter,
            [
                Comment(id="1", body="looks good", author_login="alice"),
                Comment(id="2", body="/agent retry please", author_login="alice"),
                Comment(id="3", body="thanks", author_login="alice"),
            ],
        ):
            intent = await adapter.fetch_issue_command_intent("1", None)
        assert intent is not None
        self.assertIs(intent.command, Command.RETRY)
        self.assertEqual(intent.author_login, "alice")
        self.assertEqual(intent.comment_id, "2")

    async def test_returns_none_when_no_command(self) -> None:
        adapter = RepositoryTrackerAdapter(
            platform="github", owner="o", repo="r", api_key="dummy"
        )
        with patch_fetch_new_comments(
            adapter,
            [
                Comment(id="1", body="hello"),
                Comment(id="2", body="world"),
            ],
        ):
            command = await adapter.fetch_issue_command_intent("1", None)
        self.assertIsNone(command)

    async def test_returns_first_command_in_order(self) -> None:
        adapter = RepositoryTrackerAdapter(
            platform="github", owner="o", repo="r", api_key="dummy"
        )
        with patch_fetch_new_comments(
            adapter,
            [
                Comment(id="1", body="first", author_login="bob"),
                Comment(id="2", body="/agent unblock", author_login="bob"),
                Comment(id="3", body="/agent retry", author_login="bob"),
            ],
        ):
            intent = await adapter.fetch_issue_command_intent("1", None)
        assert intent is not None
        self.assertIs(intent.command, Command.UNBLOCK)
        self.assertEqual(intent.comment_id, "2")

    async def test_swallow_adapter_exception(self) -> None:
        adapter = RepositoryTrackerAdapter(
            platform="github", owner="o", repo="r", api_key="dummy"
        )
        adapter.fetch_new_comments_since = AsyncMock(
            side_effect=RuntimeError("network down")
        )
        self.assertIsNone(
            await adapter.fetch_issue_command_intent("1", None)
        )


class TestLocalTrackerAdapterCommand(unittest.IsolatedAsyncioTestCase):
    async def test_finds_command_in_local_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # The adapter derives the comments filename from
            # `_safe_file_stem(issue_id)` (sha256-prefixed) and
            # appends `.comments.ndjson` (with a literal dot).
            digest = hashlib.sha256("1".encode("utf-8")).hexdigest()[:12]
            comments_path = Path(tmp) / f"1-{digest}.comments.ndjson"
            comments_path.write_text(
                json.dumps({"id": "1", "body": "human chat"}) + "\n"
                + json.dumps({"id": "2", "body": "/agent follow-up please"}) + "\n",
                encoding="utf-8",
            )
            adapter = LocalTrackerAdapter(issues_path=tmp)
            intent = await adapter.fetch_issue_command_intent("1", None)
            assert intent is not None
            self.assertIs(intent.command, Command.FOLLOWUP)

    async def test_no_command_in_local_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            digest = hashlib.sha256("1".encode("utf-8")).hexdigest()[:12]
            comments_path = Path(tmp) / f"1-{digest}.comments.ndjson"
            comments_path.write_text(
                json.dumps({"id": "1", "body": "hello"}) + "\n",
                encoding="utf-8",
            )
            adapter = LocalTrackerAdapter(issues_path=tmp)
            command = await adapter.fetch_issue_command_intent("1", None)
            self.assertIsNone(command)


# ---------------------------------------------------------------------------
# IssueRegistry.command_cursor
# ---------------------------------------------------------------------------


class TestCommandCursor(unittest.TestCase):
    def test_default_none(self) -> None:
        record = IssueRecord(issue_id="1", issue_identifier="ISSUE-1")
        self.assertIsNone(record.command_cursor)

    def test_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            reg = IssueRegistry(path)
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_intent("1", Intent.RETRY, source="command", command="/agent retry")
            record = reg.get("1")
            assert record is not None
            record.command_cursor = "comment-42"
            reg._save()

            reloaded = IssueRegistry(path)
            r2 = reloaded.get("1")
            assert r2 is not None
            self.assertEqual(r2.command_cursor, "comment-42")
            self.assertEqual(r2.last_command, "/agent retry")

    def test_back_compat_old_json(self) -> None:
        """A pre-Sub-D registry.json loads with command_cursor=None."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            old_payload = {
                "1": {
                    "issue_id": "1",
                    "issue_identifier": "ISSUE-1",
                    "status": "completed",
                }
            }
            path.write_text(json.dumps(old_payload), encoding="utf-8")
            reg = IssueRegistry(path)
            record = reg.get("1")
            assert record is not None
            self.assertIsNone(record.command_cursor)


from extensions.orchestrator.issue_registry import IssueRecord  # noqa: E402


# ---------------------------------------------------------------------------
# Orchestrator command acknowledgement
# ---------------------------------------------------------------------------


def _make_orchestrator(
    *,
    tracker: Any,
    registry: IssueRegistry,
) -> Orchestrator:
    workflow = WorkflowConfig()
    orch = Orchestrator.__new__(Orchestrator)
    orch.workflow = workflow
    orch.tracker = tracker
    orch.workspace = MagicMock()
    orch.agent_runner = MagicMock()
    orch.status_dashboard = MagicMock()
    orch._registry = registry
    orch._state = MagicMock()
    return orch


class TestPostCommandAcknowledgement(unittest.IsolatedAsyncioTestCase):
    async def test_posts_comment_and_updates_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")

            created = Comment(id="ack-7", body="placeholder")
            tracker = MagicMock()
            tracker.create_comment = AsyncMock(return_value=created)

            orch = _make_orchestrator(tracker=tracker, registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            comment_id = await orch._post_command_acknowledgement(issue, Command.RETRY)

            self.assertEqual(comment_id, "ack-7")
            tracker.create_comment.assert_awaited_once()
            kwargs = tracker.create_comment.await_args
            self.assertEqual(kwargs.args[0], "1")
            self.assertIn("/agent retry", kwargs.args[1])

            # Cursor was updated.
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.command_cursor, "ack-7")

    async def test_handles_create_comment_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")

            tracker = MagicMock()
            tracker.create_comment = AsyncMock(side_effect=RuntimeError("api down"))

            orch = _make_orchestrator(tracker=tracker, registry=reg)
            issue = Issue(id="1", identifier="ISSUE-1", title="x")
            comment_id = await orch._post_command_acknowledgement(issue, Command.RETRY)

            self.assertIsNone(comment_id)
            # Cursor was NOT updated because the comment failed.
            record = reg.get("1")
            assert record is not None
            self.assertIsNone(record.command_cursor)


# ---------------------------------------------------------------------------
# Orchestrator merge of label + command intent
# ---------------------------------------------------------------------------


class TestOrchestratorIntentMerge(unittest.IsolatedAsyncioTestCase):
    async def test_label_only_intent(self) -> None:
        orch = _make_orchestrator(tracker=MagicMock(), registry=IssueRegistry(Path(tempfile.mkdtemp()) / "r.json"))
        orch.tracker.extract_intent_from_labels = AsyncMock(return_value=Intent.RETRY)
        orch.tracker.fetch_issue_command_intent = AsyncMock(return_value=None)

        issue = Issue(id="1", identifier="ISSUE-1", title="x", labels=["agent:retry"])
        intent, command_intent = await orch._resolve_intent(issue)
        self.assertIs(intent, Intent.RETRY)
        self.assertIsNone(command_intent)

    async def test_command_only_intent(self) -> None:
        orch = _make_orchestrator(tracker=MagicMock(), registry=IssueRegistry(Path(tempfile.mkdtemp()) / "r.json"))
        orch.tracker.extract_intent_from_labels = AsyncMock(return_value=Intent.NONE)
        orch.tracker.fetch_issue_command_intent = AsyncMock(
            return_value=CommandIntent(
                command=Command.FOLLOWUP,
                author_login="alice",
                comment_id="c-2",
            )
        )

        issue = Issue(id="1", identifier="ISSUE-1", title="x")
        intent, command_intent = await orch._resolve_intent(issue)
        self.assertIs(intent, Intent.FOLLOWUP)
        assert command_intent is not None
        self.assertIs(command_intent.command, Command.FOLLOWUP)
        self.assertEqual(command_intent.author_login, "alice")

    async def test_command_beats_label(self) -> None:
        orch = _make_orchestrator(tracker=MagicMock(), registry=IssueRegistry(Path(tempfile.mkdtemp()) / "r.json"))
        orch.tracker.extract_intent_from_labels = AsyncMock(return_value=Intent.RETRY)
        orch.tracker.fetch_issue_command_intent = AsyncMock(
            return_value=CommandIntent(
                command=Command.FOLLOWUP, author_login="alice"
            )
        )

        issue = Issue(id="1", identifier="ISSUE-1", title="x", labels=["agent:retry"])
        intent, command_intent = await orch._resolve_intent(issue)
        # FOLLOWUP is more conservative → wins.
        self.assertIs(intent, Intent.FOLLOWUP)
        assert command_intent is not None
        self.assertIs(command_intent.command, Command.FOLLOWUP)

    async def test_blocked_label_sticky_against_unblock(self) -> None:
        orch = _make_orchestrator(tracker=MagicMock(), registry=IssueRegistry(Path(tempfile.mkdtemp()) / "r.json"))
        orch.tracker.extract_intent_from_labels = AsyncMock(return_value=Intent.BLOCKED)
        orch.tracker.fetch_issue_command_intent = AsyncMock(
            return_value=CommandIntent(
                command=Command.UNBLOCK, author_login="alice"
            )
        )

        issue = Issue(id="1", identifier="ISSUE-1", title="x", labels=["agent:blocked"])
        intent, command_intent = await orch._resolve_intent(issue)
        # merge_intents returns BLOCKED; the orchestrator then handles
        # the UNBLOCK side-effect (status reset) separately.
        self.assertIs(intent, Intent.BLOCKED)
        assert command_intent is not None
        self.assertIs(command_intent.command, Command.UNBLOCK)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def patch_fetch_new_comments(adapter: Any, comments: list[Comment]) -> Any:
    """Monkey-patch `adapter.fetch_new_comments_since` to return `comments`."""
    from unittest.mock import patch as _patch
    return _patch.object(
        adapter,
        "fetch_new_comments_since",
        new=AsyncMock(return_value=comments),
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)

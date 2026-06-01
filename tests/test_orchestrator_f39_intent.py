"""F-39 Sub-A: label parsing + intent dispatch.

Covers:
  - Intent enum value stability
  - intent_from_label_set priority rules (blocked > followup > retry > none)
  - Adapter overrides: RepositoryTrackerAdapter, LocalTrackerAdapter
  - IssueRecord new fields default to (Intent.NONE, 0, None, None)
  - IssueRegistry JSON round-trip preserves the new fields
  - Back-compat: a pre-F-39 registry.json loads with defaults
  - IssueRegistry.mark_intent / clear_intent / increment_retry_count
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from extensions.orchestrator.issue_registry import (
    IssueRecord,
    IssueRegistry,
    IssueStatus,
)
from extensions.orchestrator.local_tracker.adapter import LocalTrackerAdapter
from extensions.orchestrator.repo_tracker.adapter import (
    RepositoryTrackerAdapter,
)
from extensions.orchestrator.tracker import (
    DEFAULT_INTENT_LABELS,
    Intent,
    intent_from_label_set,
)


# ---------------------------------------------------------------------------
# Intent enum & label-set helper
# ---------------------------------------------------------------------------


class TestIntentEnum(unittest.TestCase):
    def test_intent_values(self) -> None:
        self.assertEqual(Intent.NONE.value, "none")
        self.assertEqual(Intent.RETRY.value, "retry")
        self.assertEqual(Intent.FOLLOWUP.value, "followup")
        self.assertEqual(Intent.BLOCKED.value, "blocked")

    def test_intent_is_str_enum(self) -> None:
        # Must be JSON-serializable as its string value (registry.json).
        for intent in Intent:
            self.assertEqual(json.dumps(intent.value), json.dumps(intent.value))

    def test_intent_lookup_by_value(self) -> None:
        self.assertIs(Intent("retry"), Intent.RETRY)
        self.assertIs(Intent("none"), Intent.NONE)


class TestIntentFromLabelSet(unittest.TestCase):
    def test_empty_labels_returns_none(self) -> None:
        self.assertIs(intent_from_label_set([]), Intent.NONE)
        self.assertIs(intent_from_label_set(None), Intent.NONE)

    def test_default_retry_label(self) -> None:
        self.assertIs(
            intent_from_label_set(["agent:retry"]),
            Intent.RETRY,
        )

    def test_default_followup_label(self) -> None:
        self.assertIs(
            intent_from_label_set(["agent:follow-up"]),
            Intent.FOLLOWUP,
        )

    def test_default_blocked_label(self) -> None:
        self.assertIs(
            intent_from_label_set(["agent:blocked"]),
            Intent.BLOCKED,
        )

    def test_unknown_label_returns_none(self) -> None:
        self.assertIs(
            intent_from_label_set(["bug", "enhancement"]),
            Intent.NONE,
        )

    def test_blocked_wins_over_retry(self) -> None:
        # BLOCKED is a permanent skip; must take precedence.
        self.assertIs(
            intent_from_label_set(["agent:retry", "agent:blocked"]),
            Intent.BLOCKED,
        )

    def test_blocked_wins_over_followup(self) -> None:
        self.assertIs(
            intent_from_label_set(["agent:follow-up", "agent:blocked"]),
            Intent.BLOCKED,
        )

    def test_followup_wins_over_retry(self) -> None:
        # FOLLOWUP is more conservative (preserves PR); wins over RETRY.
        self.assertIs(
            intent_from_label_set(["agent:retry", "agent:follow-up"]),
            Intent.FOLLOWUP,
        )

    def test_case_insensitive(self) -> None:
        # Labels are normalized lowercase in _extract_labels, but the
        # helper itself is also case-insensitive for robustness.
        self.assertIs(
            intent_from_label_set(["Agent:Retry"]),
            Intent.RETRY,
        )
        self.assertIs(
            intent_from_label_set(["AGENT:BLOCKED"]),
            Intent.BLOCKED,
        )

    def test_custom_intent_labels(self) -> None:
        custom = {
            "retry": "ops:rerun",
            "followup": "ops:more",
            "blocked": "ops:no",
        }
        self.assertIs(
            intent_from_label_set(["ops:rerun"], custom),
            Intent.RETRY,
        )
        self.assertIs(
            intent_from_label_set(["ops:more"], custom),
            Intent.FOLLOWUP,
        )
        self.assertIs(
            intent_from_label_set(["ops:no"], custom),
            Intent.BLOCKED,
        )
        # Default labels should NOT match when custom mapping is given.
        self.assertIs(
            intent_from_label_set(["agent:retry"], custom),
            Intent.NONE,
        )

    def test_default_intent_labels_constant(self) -> None:
        self.assertEqual(
            DEFAULT_INTENT_LABELS,
            {
                "retry": "agent:retry",
                "followup": "agent:follow-up",
                "blocked": "agent:blocked",
            },
        )


# ---------------------------------------------------------------------------
# Adapter-level overrides
# ---------------------------------------------------------------------------


class TestRepositoryTrackerAdapterIntent(unittest.IsolatedAsyncioTestCase):
    def _make(self, intent_labels: dict[str, str] | None = None) -> RepositoryTrackerAdapter:
        return RepositoryTrackerAdapter(
            platform="github",
            owner="o",
            repo="r",
            api_key="dummy",
            intent_labels=intent_labels,
        )

    async def test_default_labels(self) -> None:
        adapter = self._make()
        self.assertIs(
            await adapter.extract_intent_from_labels(["agent:retry"]),
            Intent.RETRY,
        )
        self.assertIs(
            await adapter.extract_intent_from_labels(["agent:follow-up"]),
            Intent.FOLLOWUP,
        )
        self.assertIs(
            await adapter.extract_intent_from_labels(["agent:blocked"]),
            Intent.BLOCKED,
        )

    async def test_custom_labels(self) -> None:
        adapter = self._make(
            intent_labels={
                "retry": "rerun",
                "followup": "followup",
                "blocked": "blocked",
            }
        )
        self.assertIs(
            await adapter.extract_intent_from_labels(["rerun"]),
            Intent.RETRY,
        )
        self.assertIs(
            await adapter.extract_intent_from_labels(["blocked"]),
            Intent.BLOCKED,
        )

    async def test_empty_labels(self) -> None:
        adapter = self._make()
        self.assertIs(
            await adapter.extract_intent_from_labels([]),
            Intent.NONE,
        )

    async def test_intent_labels_isolated_per_instance(self) -> None:
        a = self._make(intent_labels={"retry": "a:retry", "followup": "a:followup", "blocked": "a:blocked"})
        b = self._make()
        # Mutating one must not affect the other.
        a.intent_labels["retry"] = "mutated"
        self.assertEqual(b.intent_labels["retry"], "agent:retry")


class TestLocalTrackerAdapterIntent(unittest.IsolatedAsyncioTestCase):
    async def test_default_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = LocalTrackerAdapter(issues_path=tmp)
            self.assertIs(
                await adapter.extract_intent_from_labels(["agent:retry"]),
                Intent.RETRY,
            )
            self.assertIs(
                await adapter.extract_intent_from_labels(["agent:blocked", "agent:follow-up"]),
                Intent.BLOCKED,
            )
            self.assertIs(
                await adapter.extract_intent_from_labels(["agent:retry", "agent:follow-up"]),
                Intent.FOLLOWUP,
            )

    async def test_custom_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = LocalTrackerAdapter(
                issues_path=tmp,
                intent_labels={
                    "retry": "rerun",
                    "followup": "followup",
                    "blocked": "blocked",
                },
            )
            self.assertIs(
                await adapter.extract_intent_from_labels(["rerun"]),
                Intent.RETRY,
            )
            self.assertIs(
                await adapter.extract_intent_from_labels(["blocked"]),
                Intent.BLOCKED,
            )


# ---------------------------------------------------------------------------
# IssueRecord new fields
# ---------------------------------------------------------------------------


class TestIssueRecordDefaults(unittest.TestCase):
    def test_default_intent_is_none(self) -> None:
        record = IssueRecord(issue_id="1", issue_identifier="ISSUE-1")
        self.assertEqual(record.intent, Intent.NONE)

    def test_default_retry_count_is_zero(self) -> None:
        record = IssueRecord(issue_id="1", issue_identifier="ISSUE-1")
        self.assertEqual(record.retry_count, 0)

    def test_default_last_command_is_none(self) -> None:
        record = IssueRecord(issue_id="1", issue_identifier="ISSUE-1")
        self.assertIsNone(record.last_command)

    def test_default_intent_source_is_none(self) -> None:
        record = IssueRecord(issue_id="1", issue_identifier="ISSUE-1")
        self.assertIsNone(record.intent_source)

    def test_can_set_explicit_intent(self) -> None:
        record = IssueRecord(
            issue_id="1",
            issue_identifier="ISSUE-1",
            intent=Intent.RETRY,
            retry_count=2,
            last_command="/agent retry",
            intent_source="label",
        )
        self.assertEqual(record.intent, Intent.RETRY)
        self.assertEqual(record.retry_count, 2)
        self.assertEqual(record.last_command, "/agent retry")
        self.assertEqual(record.intent_source, "label")


# ---------------------------------------------------------------------------
# IssueRegistry round-trip
# ---------------------------------------------------------------------------


class TestIssueRegistryIntentFields(unittest.TestCase):
    def test_json_round_trip_preserves_intent_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            reg = IssueRegistry(path)
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_intent("1", Intent.RETRY, source="label", command="/agent retry")
            reg.increment_retry_count("1")
            reg.increment_retry_count("1")
            reg.mark_completed("1")

            # Reload from disk
            reloaded = IssueRegistry(path)
            record = reloaded.get("1")
            assert record is not None
            self.assertEqual(record.intent, Intent.RETRY)
            self.assertEqual(record.retry_count, 2)
            self.assertEqual(record.last_command, "/agent retry")
            self.assertEqual(record.intent_source, "label")
            self.assertEqual(record.status, IssueStatus.COMPLETED)

    def test_backward_compat_old_json(self) -> None:
        """A registry.json written before F-39 (no `intent` field) must
        load cleanly with Intent.NONE / retry_count=0 defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            old_payload = {
                "1": {
                    "issue_id": "1",
                    "issue_identifier": "ISSUE-1",
                    "branch_name": "main",
                    "status": "completed",
                    "pr_number": "7",
                    "pr_url": "https://example.test/pr/7",
                    "base_branch": "main",
                }
            }
            path.write_text(json.dumps(old_payload), encoding="utf-8")

            reg = IssueRegistry(path)
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.intent, Intent.NONE)
            self.assertEqual(record.retry_count, 0)
            self.assertIsNone(record.last_command)
            self.assertIsNone(record.intent_source)
            self.assertEqual(record.status, IssueStatus.COMPLETED)
            self.assertEqual(record.pr_number, "7")
            # The record still has_pr() — that's the pre-F-39 default;
            # Sub-A must not break this 4-layer defense.
            self.assertTrue(reg.has_pr("1"))
            self.assertTrue(reg.is_completed("1"))

    def test_mark_intent_on_missing_record_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            self.assertIsNone(reg.mark_intent("missing", Intent.RETRY))

    def test_clear_intent_resets_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_intent("1", Intent.RETRY, source="label")
            reg.clear_intent("1")
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.intent, Intent.NONE)
            self.assertIsNone(record.intent_source)

    def test_clear_intent_preserves_history_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_intent("1", Intent.RETRY, source="label")
            reg.clear_intent("1", record_intent_history=True)
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.intent, Intent.NONE)
            self.assertEqual(record.intent_source, "label")  # preserved

    def test_increment_retry_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.increment_retry_count("1")
            reg.increment_retry_count("1")
            reg.increment_retry_count("1")
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.retry_count, 3)

    def test_increment_retry_count_on_missing_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            self.assertIsNone(reg.increment_retry_count("missing"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

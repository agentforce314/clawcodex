"""WS-10: Structural parity — QueryState fields and transitions match TS.

Verifies:
- QueryState fields match ts_query_transitions.json
- Transition reasons match
- Recovery constants match
- QueryConfig fields match
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.query.transitions import (
    ContinueReason,
    QueryState,
    Terminal,
    Transition,
)
from src.query.config import QueryConfig, build_query_config
from src.query.query import (
    ESCALATED_MAX_TOKENS,
    MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
    QueryParams,
)

REF_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "reference_data"


def _load_json(name: str) -> dict:
    return json.loads((REF_DIR / name).read_text())


class TestQueryStateFieldsParity(unittest.TestCase):
    """QueryState fields match TS query.ts state shape."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_query_transitions.json")

    def test_all_state_fields_present(self) -> None:
        expected_fields = self.snapshot["query_state_fields"]
        state = QueryState.__dataclass_fields__
        for field_name in expected_fields:
            self.assertIn(
                field_name, state,
                f"QueryState missing field '{field_name}'",
            )

    def test_state_has_messages_field(self) -> None:
        state = QueryState(messages=[], tool_use_context=None)
        self.assertIsInstance(state.messages, list)

    def test_state_has_turn_count_default_1(self) -> None:
        state = QueryState(messages=[], tool_use_context=None)
        self.assertEqual(state.turn_count, 1)

    def test_state_has_recovery_count_default_0(self) -> None:
        state = QueryState(messages=[], tool_use_context=None)
        self.assertEqual(state.max_output_tokens_recovery_count, 0)

    def test_state_has_reactive_compact_default_false(self) -> None:
        state = QueryState(messages=[], tool_use_context=None)
        self.assertFalse(state.has_attempted_reactive_compact)

    def test_state_transition_default_none(self) -> None:
        state = QueryState(messages=[], tool_use_context=None)
        self.assertIsNone(state.transition)


class TestTransitionReasonsParity(unittest.TestCase):
    """Transition reasons match TS query.ts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_query_transitions.json")

    def test_all_transition_reasons_present(self) -> None:
        ts_reasons = set(self.snapshot["transition_reasons"])
        # ContinueReason is a Literal type — get its args
        import typing
        py_reasons = set(typing.get_args(ContinueReason))
        self.assertEqual(ts_reasons, py_reasons)

    def test_transition_can_be_created_for_each_reason(self) -> None:
        for reason in self.snapshot["transition_reasons"]:
            t = Transition(reason=reason)
            self.assertEqual(t.reason, reason)

    def test_transition_is_frozen(self) -> None:
        t = Transition(reason="next_turn")
        with self.assertRaises(AttributeError):
            t.reason = "max_output_tokens_recovery"  # type: ignore

    def test_terminal_has_reason_field(self) -> None:
        term = Terminal(reason="end_turn")
        self.assertEqual(term.reason, "end_turn")


class TestRecoveryConstantsParity(unittest.TestCase):
    """Recovery constants match TS query.ts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_query_transitions.json")

    def test_escalated_max_tokens_matches(self) -> None:
        expected = self.snapshot["recovery_constants"]["ESCALATED_MAX_TOKENS"]
        self.assertEqual(ESCALATED_MAX_TOKENS, expected)

    def test_max_recovery_limit_matches(self) -> None:
        expected = self.snapshot["recovery_constants"]["MAX_OUTPUT_TOKENS_RECOVERY_LIMIT"]
        self.assertEqual(MAX_OUTPUT_TOKENS_RECOVERY_LIMIT, expected)


class TestQueryConfigFieldsParity(unittest.TestCase):
    """QueryConfig fields match TS queryConfig."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_query_transitions.json")

    def test_all_config_fields_present(self) -> None:
        expected_fields = self.snapshot["query_config_fields"]
        config = QueryConfig.__dataclass_fields__
        for field_name in expected_fields:
            self.assertIn(
                field_name, config,
                f"QueryConfig missing field '{field_name}'",
            )

    def test_build_query_config_returns_frozen(self) -> None:
        config = build_query_config()
        with self.assertRaises(AttributeError):
            config.session_id = "new"  # type: ignore

    def test_build_query_config_has_session_id(self) -> None:
        config = build_query_config()
        self.assertIsInstance(config.session_id, str)
        self.assertTrue(len(config.session_id) > 0)

    def test_query_params_has_pipeline_config(self) -> None:
        """QueryParams should accept pipeline_config for compression."""
        self.assertIn("pipeline_config", QueryParams.__dataclass_fields__)


if __name__ == "__main__":
    unittest.main()

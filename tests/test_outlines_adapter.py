"""Tests for Outlines adapter (Task #7)."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from src.agent._outlines_adapter import (
    OutlinesStructuredOutput,
    ToolCallDecision,
    TokenBudgetAnalysis,
    create_structured_output_handler,
    is_outlines_available,
)


class TestOutlinesAvailable:
    def test_outlines_is_available(self):
        assert is_outlines_available() is True


class TestOutlinesStructuredOutput:
    def test_handler_initialization(self):
        handler = OutlinesStructuredOutput("gpt-4o")
        assert handler.model_name == "gpt-4o"

    def test_handler_default_model(self):
        handler = OutlinesStructuredOutput()
        assert handler.model_name == "gpt-4o"

    def test_create_structured_output_handler(self):
        handler = create_structured_output_handler("gpt-4o-mini")
        assert handler.model_name == "gpt-4o-mini"


class TestTokenBudgetAnalysis:
    def test_model_fields(self):
        fields = list(TokenBudgetAnalysis.model_fields.keys())
        assert "current_usage" in fields
        assert "threshold" in fields
        assert "should_compact" in fields
        assert "recommended_strategy" in fields
        assert "priority_indices" in fields
        assert "confidence" in fields

    def test_model_validation(self):
        analysis = TokenBudgetAnalysis(
            current_usage=50000,
            threshold=100000,
            should_compact=False,
            recommended_strategy="none",
            priority_indices=[],
            confidence=0.95,
        )
        assert analysis.current_usage == 50000
        assert analysis.should_compact is False
        assert analysis.recommended_strategy == "none"


class TestToolCallDecision:
    def test_model_fields(self):
        fields = list(ToolCallDecision.model_fields.keys())
        assert "should_call_tool" in fields
        assert "tool_name" in fields
        assert "reasoning" in fields
        assert "safety_level" in fields

    def test_model_with_tool_call(self):
        decision = ToolCallDecision(
            should_call_tool=True,
            tool_name="Bash",
            reasoning="User requested git status",
            safety_level="read_only",
        )
        assert decision.should_call_tool is True
        assert decision.tool_name == "Bash"
        assert decision.safety_level == "read_only"

    def test_model_without_tool_call(self):
        decision = ToolCallDecision(
            should_call_tool=False,
            tool_name=None,
            reasoning="No tool needed for this query",
            safety_level="safe",
        )
        assert decision.should_call_tool is False
        assert decision.tool_name is None


class TestBackwardCompatibility:
    def test_outlines_handler_instantiable(self):
        """Ensure OutlinesStructuredOutput can be instantiated."""
        handler = OutlinesStructuredOutput()
        assert handler is not None
        assert hasattr(handler, "generate_structured")
        assert hasattr(handler, "generate_with_fallback")

    def test_create_handler_factory(self):
        """Ensure factory function works."""
        handler = create_structured_output_handler()
        assert isinstance(handler, OutlinesStructuredOutput)
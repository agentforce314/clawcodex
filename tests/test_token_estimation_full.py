"""Tests for R2-WS-9: Extended token estimation."""

from __future__ import annotations

import pytest

from src.token_estimation import (
    estimate_cache_aware_tokens,
    estimate_image_tokens,
    estimate_system_prompt_sections_tokens,
    estimate_system_prompt_tokens,
    estimate_tool_schema_tokens,
    rough_token_count_estimation_per_block_type,
)


class TestEstimateToolSchema:
    def test_small_schema(self):
        schema = {"name": "Read", "description": "Read a file", "input_schema": {"type": "object"}}
        tokens = estimate_tool_schema_tokens(schema)
        assert tokens > 0

    def test_large_schema(self):
        schema = {"name": "Bash", "description": "Execute command " * 100, "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}}}
        tokens = estimate_tool_schema_tokens(schema)
        assert tokens > 50

    def test_empty_schema(self):
        tokens = estimate_tool_schema_tokens({})
        assert tokens >= 0


class TestEstimateSystemPrompt:
    def test_prompt_tokens(self):
        prompt = "You are Claude, an AI assistant." * 10
        tokens = estimate_system_prompt_tokens(prompt)
        assert tokens > 0

    def test_sections_tokens(self):
        sections = {
            "identity": "You are Claude.",
            "tools": "Available tools: Read, Write.",
            "environment": "OS: macOS, Shell: zsh",
        }
        result = estimate_system_prompt_sections_tokens(sections)
        assert len(result) == 3
        assert all(v > 0 for v in result.values())


class TestEstimateImageTokens:
    def test_small_image(self):
        tokens = estimate_image_tokens(32, 32)
        assert tokens >= 85  # Minimum

    def test_large_image(self):
        tokens = estimate_image_tokens(1920, 1080)
        assert tokens > 1000

    def test_minimum_floor(self):
        tokens = estimate_image_tokens(1, 1)
        assert tokens == 85


class TestCacheAwareTokens:
    def test_no_cache(self):
        result = estimate_cache_aware_tokens(1000)
        assert result["uncached_tokens"] == 1000
        assert result["cache_read_tokens"] == 0
        assert result["effective_tokens"] == 1000

    def test_with_cache_read(self):
        result = estimate_cache_aware_tokens(1000, cache_read_tokens=500)
        assert result["uncached_tokens"] == 500
        assert result["cache_read_tokens"] == 500
        assert result["effective_tokens"] < 1000  # Cache reads are cheaper

    def test_with_cache_creation(self):
        result = estimate_cache_aware_tokens(1000, cache_creation_tokens=200)
        assert result["cache_creation_tokens"] == 200
        assert result["effective_tokens"] > 800  # Cache creation is more expensive


class TestPerBlockType:
    def test_mixed_blocks(self):
        blocks = [
            {"type": "text", "text": "hello world"},
            {"type": "text", "text": "another text"},
            {"type": "tool_use", "name": "Read", "input": {"path": "/file.py"}},
        ]
        result = rough_token_count_estimation_per_block_type(blocks)
        assert "text" in result
        assert "tool_use" in result
        assert result["text"] > 0
        assert result["tool_use"] > 0

    def test_empty_blocks(self):
        result = rough_token_count_estimation_per_block_type([])
        assert result == {}

    def test_image_blocks(self):
        blocks = [{"type": "image", "source": {"data": "base64data"}}]
        result = rough_token_count_estimation_per_block_type(blocks)
        assert "image" in result
        assert result["image"] == 2000

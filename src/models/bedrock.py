"""Bedrock model ID mapping matching TypeScript model/bedrock.ts."""

from __future__ import annotations

BEDROCK_MODEL_MAP: dict[str, str] = {
    # Claude 4
    "claude-sonnet-4-20250514": "anthropic.claude-sonnet-4-20250514-v1:0",
    "claude-opus-4-20250514": "anthropic.claude-opus-4-20250514-v1:0",
    # Claude 3.7
    "claude-3-7-sonnet-20250219": "anthropic.claude-3-7-sonnet-20250219-v1:0",
    # Claude 3.5
    "claude-3-5-sonnet-20241022": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "claude-3-5-sonnet-20240620": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-3-5-haiku-20241022": "anthropic.claude-3-5-haiku-20241022-v1:0",
    # Claude 3
    "claude-3-opus-20240229": "anthropic.claude-3-opus-20240229-v1:0",
    "claude-3-sonnet-20240229": "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude-3-haiku-20240307": "anthropic.claude-3-haiku-20240307-v1:0",
}

_REVERSE_MAP: dict[str, str] = {v: k for k, v in BEDROCK_MODEL_MAP.items()}


def to_bedrock_model_id(canonical_id: str) -> str | None:
    """Convert a canonical model ID to a Bedrock model ID."""
    return BEDROCK_MODEL_MAP.get(canonical_id)


def from_bedrock_model_id(bedrock_id: str) -> str | None:
    """Convert a Bedrock model ID back to canonical."""
    return _REVERSE_MAP.get(bedrock_id)

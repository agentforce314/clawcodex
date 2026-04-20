from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class NonNullableUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }


EMPTY_USAGE = NonNullableUsage()


def accumulate_usage(
    accumulated: NonNullableUsage,
    delta: dict[str, int] | NonNullableUsage | None,
) -> NonNullableUsage:
    if delta is None:
        return accumulated

    if isinstance(delta, NonNullableUsage):
        return NonNullableUsage(
            input_tokens=accumulated.input_tokens + delta.input_tokens,
            output_tokens=accumulated.output_tokens + delta.output_tokens,
            cache_creation_input_tokens=accumulated.cache_creation_input_tokens + delta.cache_creation_input_tokens,
            cache_read_input_tokens=accumulated.cache_read_input_tokens + delta.cache_read_input_tokens,
        )

    return NonNullableUsage(
        input_tokens=accumulated.input_tokens + delta.get("input_tokens", 0),
        output_tokens=accumulated.output_tokens + delta.get("output_tokens", 0),
        cache_creation_input_tokens=accumulated.cache_creation_input_tokens + delta.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=accumulated.cache_read_input_tokens + delta.get("cache_read_input_tokens", 0),
    )


def update_usage(target: NonNullableUsage, source: dict[str, int] | NonNullableUsage | None) -> None:
    if source is None:
        return
    if isinstance(source, NonNullableUsage):
        target.input_tokens += source.input_tokens
        target.output_tokens += source.output_tokens
        target.cache_creation_input_tokens += source.cache_creation_input_tokens
        target.cache_read_input_tokens += source.cache_read_input_tokens
    else:
        target.input_tokens += source.get("input_tokens", 0)
        target.output_tokens += source.get("output_tokens", 0)
        target.cache_creation_input_tokens += source.get("cache_creation_input_tokens", 0)
        target.cache_read_input_tokens += source.get("cache_read_input_tokens", 0)


@dataclass
class APICallLog:
    model: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    usage: NonNullableUsage = field(default_factory=NonNullableUsage)
    stop_reason: str = ""
    error: str | None = None

    @property
    def duration_ms(self) -> int:
        return int((self.end_time - self.start_time) * 1000)


def log_api_call(call_log: APICallLog) -> None:
    duration = call_log.duration_ms
    usage = call_log.usage
    logger.info(
        "API call: model=%s duration=%dms input=%d output=%d cache_read=%d cache_create=%d stop=%s%s",
        call_log.model,
        duration,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens,
        usage.cache_creation_input_tokens,
        call_log.stop_reason,
        f" error={call_log.error}" if call_log.error else "",
    )

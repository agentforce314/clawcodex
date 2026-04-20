from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueryRequest:
    prompt: str


@dataclass(frozen=True)
class QueryResponse:
    text: str

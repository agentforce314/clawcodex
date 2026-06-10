"""Shared fixtures for the workflow-engine tests.

``FakeRunner`` stands in for the production ``AgentRunner`` so the engine can be
exercised end-to-end without a live model: it records every ``AgentSpec``, can
delay (for concurrency tests), honors the abort signal, and either uses a
caller-supplied ``handler`` or a sensible default (text → ``r{index}``, schema →
``{"echo": prompt}``).
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pytest

from src.workflow.types import AgentOutcome, AgentSpec


@dataclass
class FakeRunner:
    handler: Optional[Callable[[AgentSpec, int], Any]] = None
    delay: float = 0.0
    calls: list[AgentSpec] = field(default_factory=list)
    peak: int = 0
    _active: int = 0

    async def run(self, spec: AgentSpec, *, abort, index: str) -> AgentOutcome:
        self.calls.append(spec)
        self._active += 1
        self.peak = max(self.peak, self._active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if abort.signal.aborted:
                return AgentOutcome(skipped=True)
            if self.handler is not None:
                out = self.handler(spec, index)
                if inspect.isawaitable(out):
                    out = await out
                return out
            if spec.schema is not None:
                return AgentOutcome(structured={"echo": spec.prompt}, tokens=10)
            return AgentOutcome(text=f"r{index}", tokens=5)
        finally:
            self._active -= 1

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def make_runner():
    def _make(handler=None, delay: float = 0.0) -> FakeRunner:
        return FakeRunner(handler=handler, delay=delay)

    return _make


def instance_from_schema(schema: Any) -> Any:
    """Build a minimal value satisfying a JSON Schema (for driving demos)."""
    if not isinstance(schema, dict):
        return "x"
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    stype = schema.get("type")
    if stype == "object":
        props = schema.get("properties", {})
        return {key: instance_from_schema(sub) for key, sub in props.items()}
    if stype == "array":
        items = schema.get("items")
        return [instance_from_schema(items)] if isinstance(items, dict) else []
    if stype == "integer":
        return 1
    if stype == "number":
        return 1.0
    if stype == "boolean":
        return True
    return "x"


@dataclass
class SchemaFakeRunner:
    """A runner that returns schema-derived stubs and content-bearing text, so a
    real demo script can run end-to-end without a model."""

    calls: list[AgentSpec] = field(default_factory=list)

    async def run(self, spec: AgentSpec, *, abort, index: str) -> AgentOutcome:
        self.calls.append(spec)
        if spec.schema is not None:
            return AgentOutcome(structured=instance_from_schema(spec.schema), tokens=10)
        # Text with a question mark + multiple lines so line/`?`-filtering demos
        # produce non-empty output.
        text = "What should a buyer consider?\nFirst point\nSecond point?\nThird point?"
        return AgentOutcome(text=text, tokens=5)


@pytest.fixture
def schema_runner() -> SchemaFakeRunner:
    return SchemaFakeRunner()

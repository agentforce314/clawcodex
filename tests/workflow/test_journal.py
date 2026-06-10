"""Tests for the resume journal (call-path keying + per-key matching)."""

from __future__ import annotations

from src.workflow.journal import MISS, Journal, JournalRecord, fingerprint
from src.workflow.types import AgentSpec


def _spec(prompt="p", **kw):
    return AgentSpec(prompt=prompt, **kw)


def test_fingerprint_is_stable_and_distinguishing():
    a = _spec("hello")
    assert fingerprint(a) == fingerprint(_spec("hello"))
    assert fingerprint(a) != fingerprint(_spec("world"))
    assert fingerprint(_spec("p", model="opus")) != fingerprint(_spec("p", model="haiku"))
    assert fingerprint(_spec("p", schema={"type": "object"})) != fingerprint(_spec("p"))


def test_lookup_hit_returns_cached_result():
    spec = _spec("x")
    j = Journal({(0,): JournalRecord(fingerprint(spec), "cached-value")})
    assert j.lookup((0,), spec) == "cached-value"


def test_lookup_miss_when_fingerprint_differs():
    j = Journal({(0,): JournalRecord(fingerprint(_spec("old")), "v")})
    assert j.lookup((0,), _spec("new")) is MISS


def test_per_key_independence():
    # Path-based keys: changing one call does NOT invalidate an independent
    # sibling at a different path (more precise than linear-prefix divergence).
    prior = {
        (0,): JournalRecord(fingerprint(_spec("a")), "ra"),
        (1,): JournalRecord(fingerprint(_spec("b")), "rb"),
        (2,): JournalRecord(fingerprint(_spec("c")), "rc"),
    }
    j = Journal(prior)
    assert j.lookup((0,), _spec("a")) == "ra"        # hit
    assert j.lookup((1,), _spec("CHANGED")) is MISS  # changed -> miss
    assert j.lookup((2,), _spec("c")) == "rc"        # independent -> still hit


def test_nested_path_keys_are_distinct():
    spec = _spec("x")
    j = Journal({(0, 1, 0): JournalRecord(fingerprint(spec), "deep")})
    assert j.lookup((0, 1, 0), spec) == "deep"
    assert j.lookup((0, 1, 1), spec) is MISS


def test_record_and_roundtrip():
    j = Journal()
    j.record((0,), _spec("a"), {"r": 1})
    j.record((1, 0), _spec("b"), "text")
    assert set(j.records.keys()) == {(0,), (1, 0)}
    restored = Journal.load(j.to_json())
    assert restored[(0,)].result == {"r": 1}
    assert restored[(1, 0)].result == "text"
    assert restored[(0,)].fingerprint == fingerprint(_spec("a"))

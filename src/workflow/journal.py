"""Per-run journal enabling same-session resume.

Each ``agent()`` call is keyed by its **deterministic call-path** (see
:mod:`src.workflow.callpath`) plus a fingerprint of its ``(prompt, opts)``. On
resume a call is served from cache iff both its path and fingerprint match the
prior run. Because the path is structural (not spawn-order), an unchanged script
+ args produces identical keys regardless of subagent timing, and a changed
call only invalidates calls whose *inputs* therefore change (their fingerprints
differ) — independent branches stay cached.

The on-disk form is a JSON file under the session directory (wired by the
Workflow tool); the in-memory form (``dict[CallKey, JournalRecord]``) is what
the engine reads/writes during a run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .callpath import CallKey, key_from_str, key_to_str
from .types import AgentSpec

_MISS = object()
#: Sentinel returned by :meth:`Journal.lookup` for a cache miss.
MISS = _MISS


@dataclass(frozen=True)
class JournalRecord:
    fingerprint: str
    result: Any


def fingerprint(spec: AgentSpec) -> str:
    """Stable hash of the parts of a call that determine its result."""
    payload = json.dumps(
        {
            "prompt": spec.prompt,
            "schema": spec.schema,
            "model": spec.model,
            "agent_type": spec.agent_type,
            "isolation": spec.isolation,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Journal:
    def __init__(self, prior: Optional[Mapping[CallKey, JournalRecord]] = None) -> None:
        self._prior = dict(prior or {})
        self._records: dict[CallKey, JournalRecord] = {}

    def lookup(self, key: CallKey, spec: AgentSpec):
        """Return the cached result for an unchanged call at ``key``, else MISS."""
        prior = self._prior.get(key)
        if prior is not None and prior.fingerprint == fingerprint(spec):
            return prior.result
        return _MISS

    def record(self, key: CallKey, spec: AgentSpec, result: Any) -> None:
        self._records[key] = JournalRecord(fingerprint(spec), result)

    @property
    def records(self) -> dict[CallKey, JournalRecord]:
        return dict(self._records)

    # ── persistence (string-keyed for JSON) ───────────────────────────────────

    def to_json(self) -> str:
        return json.dumps(
            {key_to_str(k): {"fingerprint": r.fingerprint, "result": r.result} for k, r in self._records.items()},
            default=str,
        )

    @staticmethod
    def load(text: str) -> dict[CallKey, JournalRecord]:
        raw = json.loads(text)
        return {key_from_str(k): JournalRecord(v["fingerprint"], v["result"]) for k, v in raw.items()}

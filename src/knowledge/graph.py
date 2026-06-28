"""Knowledge graph store + heuristic entity extraction.

Entities are keyed by (type, name); each tracks how many times it's been seen and
when last seen. ``record_from_text`` extracts entities by pattern. Persistence is a
single JSON file (default ``~/.clawcodex/knowledge/graph.json``).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_graph_path() -> Path:
    return Path.home() / ".clawcodex" / "knowledge" / "graph.json"


@dataclass
class Entity:
    name: str
    type: str  # "file" | "symbol" | "url"
    count: int = 1
    last_seen: float = 0.0
    attributes: dict[str, str] = field(default_factory=dict)


# Extraction patterns (heuristic). Kept deliberately low-noise.
_FILE_RE = re.compile(r"(?<![\w/])([\w./-]+\.(?:py|ts|tsx|js|jsx|md|json|toml|yaml|yml|rs|go|sh|txt))\b")
_SYMBOL_RE = re.compile(r"`([A-Za-z_][\w./]{1,60})`")
_URL_RE = re.compile(r"https?://[^\s)\]>'\"]+")


class KnowledgeGraph:
    """In-memory entity store with heuristic extraction + JSON persistence."""

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}  # key: f"{type}:{name}"

    @staticmethod
    def _key(name: str, etype: str) -> str:
        return f"{etype}:{name}"

    def add(self, name: str, etype: str, now: float = 0.0) -> None:
        name = name.strip()
        if not name:
            return
        key = self._key(name, etype)
        ent = self.entities.get(key)
        if ent is None:
            self.entities[key] = Entity(name=name, type=etype, count=1, last_seen=now)
        else:
            ent.count += 1
            if now:
                ent.last_seen = now

    def record_from_text(self, text: str, now: float = 0.0) -> int:
        """Extract entities from ``text``; returns the number of mentions recorded."""
        if not text:
            return 0
        n = 0
        for m in _FILE_RE.finditer(text):
            self.add(m.group(1), "file", now)
            n += 1
        for m in _SYMBOL_RE.finditer(text):
            self.add(m.group(1), "symbol", now)
            n += 1
        for m in _URL_RE.finditer(text):
            self.add(m.group(0), "url", now)
            n += 1
        return n

    def stats(self) -> dict[str, int]:
        by_type: dict[str, int] = {}
        for ent in self.entities.values():
            by_type[ent.type] = by_type.get(ent.type, 0) + 1
        return {"total": len(self.entities), **by_type}

    def top(self, limit: int = 20) -> list[Entity]:
        return sorted(self.entities.values(), key=lambda e: (-e.count, -e.last_seen, e.name))[:limit]

    def clear(self) -> None:
        self.entities.clear()

    # — persistence —
    def to_dict(self) -> dict:
        return {"entities": [asdict(e) for e in self.entities.values()]}

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeGraph":
        g = cls()
        for e in data.get("entities", []) or []:
            try:
                ent = Entity(
                    name=str(e["name"]),
                    type=str(e.get("type", "")),
                    count=int(e.get("count", 1)),
                    last_seen=float(e.get("last_seen", 0.0)),
                    attributes=dict(e.get("attributes", {})),
                )
                g.entities[cls._key(ent.name, ent.type)] = ent
            except Exception:  # noqa: BLE001 - skip malformed entries
                continue
        return g

    def save(self, path: Path | None = None) -> None:
        p = path or default_graph_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | None = None) -> "KnowledgeGraph":
        p = path or default_graph_path()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - missing/corrupt → empty graph
            return cls()

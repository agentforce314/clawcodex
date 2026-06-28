"""Knowledge graph — a lightweight, auto-populated store of entities the agent has
encountered (files, symbols, URLs), the foundation of the original's /knowledge.

This is the heuristic first version: entities are extracted from conversation text
by pattern (file paths, `backtick` symbols, URLs) rather than the original's
model-based semantic extraction + @orama search, which is a follow-up. The store,
persistence, and /knowledge command surface are functional end-to-end.
"""

from .graph import Entity, KnowledgeGraph, default_graph_path

__all__ = ["Entity", "KnowledgeGraph", "default_graph_path"]

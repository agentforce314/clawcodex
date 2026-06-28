"""Model-based semantic entity extraction for the knowledge graph (the original's
mechanism, opt-in). Asks the provider for a JSON list of entities; falls back to
[] on any error so the caller can use the heuristic instead.
"""

from __future__ import annotations

import json
import re
from typing import Any

_PROMPT = (
    "Extract the key technical entities discussed in the text below. "
    'Return ONLY a JSON array of objects with "name" and "type" '
    "(type one of: file, symbol, concept). At most 10 entities, most important first. "
    "No prose, no code fences.\n\nTEXT:\n"
)


def extract_entities_semantic(text: str, provider: Any) -> list[tuple[str, str]]:
    """Return [(name, type), …] extracted by the model; [] on any failure."""
    if not text or provider is None:
        return []
    try:
        # chat() accepts plain {role, content} dicts (MessageInput = ChatMessage | dict).
        resp = provider.chat([{"role": "user", "content": _PROMPT + text[:4000]}])
        raw = (getattr(resp, "content", "") or "").strip()
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            return []
        items = json.loads(m.group(0))
        out: list[tuple[str, str]] = []
        for it in items:
            if isinstance(it, dict) and str(it.get("name", "")).strip():
                etype = str(it.get("type", "concept")).strip().lower()
                if etype not in ("file", "symbol", "concept", "url"):
                    etype = "concept"
                out.append((str(it["name"]).strip(), etype))
        return out[:10]
    except Exception:  # noqa: BLE001 - extraction is best-effort
        return []

"""NDJSON helpers.

Port of ``typescript/src/cli/ndjsonSafeStringify.ts``.

``json.dumps`` leaves U+2028 / U+2029 unescaped by default. Those code points
are valid in JSON but are treated as line terminators by a number of
line-oriented NDJSON receivers (most notably the JavaScript ``split`` family
and therefore the Claude Code SDK ``ProcessTransport``). A truncated line
silently loses the tail of the message, so we always escape them on the
emit side. The result is still valid JSON and parses to the same value.
"""

from __future__ import annotations

import json
from typing import Any

_LINE_SEPARATORS = {
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


def ndjson_safe_dumps(value: Any) -> str:
    """Serialize ``value`` to a single NDJSON-safe JSON string.

    Mirrors ``ndjsonSafeStringify`` from the TypeScript implementation.
    """

    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if "\u2028" not in encoded and "\u2029" not in encoded:
        return encoded
    out_parts: list[str] = []
    for ch in encoded:
        replacement = _LINE_SEPARATORS.get(ch)
        out_parts.append(replacement if replacement is not None else ch)
    return "".join(out_parts)

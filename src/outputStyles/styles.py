from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OutputStyle:
    name: str
    prompt: str
    source_path: Path | None = None


BUILTIN_OUTPUT_STYLES: dict[str, OutputStyle] = {
    "default": OutputStyle(
        name="default",
        prompt="Respond clearly, concisely, and focus on the user's requested engineering task.",
    ),
    "explanatory": OutputStyle(
        name="explanatory",
        prompt="Respond with concise implementation details plus short educational notes when they improve understanding.",
    ),
}


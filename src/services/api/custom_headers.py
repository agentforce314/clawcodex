"""ch16 round-4 — ANTHROPIC_CUSTOM_HEADERS injection.

Enterprise deployments route Anthropic API traffic through a gateway / MITM
proxy that injects org auth headers (the book's ch16 system #3: "tunnel API
traffic through infrastructure that might inject credentials or terminate
TLS"). The Anthropic SDK accepts a ``default_headers`` kwarg; this module
parses the curl-style ``ANTHROPIC_CUSTOM_HEADERS`` env var into that dict.

Mirrors TS ``services/api/client.ts:530-549`` (``getCustomHeaders``): split on
newlines, parse ``Name: Value`` on the first colon, trim, skip blank /
colon-less lines. The injection mechanism itself is already proven in-repo at
``providers/openrouter_provider.py`` (``default_headers`` threading).
"""
from __future__ import annotations

import os


def parse_custom_headers(raw: str | None) -> dict[str, str]:
    """Parse curl-style ``Name: Value`` header lines (newline-separated).

    A later duplicate name wins (last-write, like a dict build). Blank lines
    and lines with no colon are skipped; the name is trimmed and must be
    non-empty; the value is everything after the first colon, trimmed.
    """
    headers: dict[str, str] = {}
    if not raw:
        return headers
    for line in raw.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        colon = line.find(":")
        if colon == -1:
            continue
        name = line[:colon].strip()
        value = line[colon + 1:].strip()
        if name:
            headers[name] = value
    return headers


def get_anthropic_custom_headers() -> dict[str, str]:
    """The parsed ``ANTHROPIC_CUSTOM_HEADERS`` env var (empty if unset)."""
    return parse_custom_headers(os.environ.get("ANTHROPIC_CUSTOM_HEADERS"))

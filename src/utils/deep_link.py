"""Deep link handling.

Mirrors TypeScript utils/deepLink.ts — parses and constructs deep links
for sharing prompts, sessions, and configurations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse


DEEP_LINK_SCHEME = "claude-code"
DEEP_LINK_HOST = "app"


@dataclass
class DeepLink:
    """A parsed deep link."""
    action: str
    params: dict[str, str] = field(default_factory=dict)

    @property
    def prompt(self) -> str | None:
        return self.params.get("prompt")

    @property
    def session_id(self) -> str | None:
        return self.params.get("session_id")

    @property
    def model(self) -> str | None:
        return self.params.get("model")

    def to_url(self) -> str:
        """Serialize to a URL string."""
        query = urlencode(self.params)
        return f"{DEEP_LINK_SCHEME}://{DEEP_LINK_HOST}/{self.action}?{query}"


def parse_deep_link(url: str) -> DeepLink | None:
    """Parse a deep link URL into a DeepLink object.

    Returns None if the URL is not a valid deep link.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme != DEEP_LINK_SCHEME:
        return None

    action = parsed.path.lstrip("/")
    if not action:
        return None

    query_params = parse_qs(parsed.query)
    params = {k: v[0] for k, v in query_params.items() if v}

    return DeepLink(action=action, params=params)


def create_prompt_link(prompt: str, model: str | None = None) -> str:
    """Create a deep link that opens Claude Code with a prompt."""
    params: dict[str, str] = {"prompt": prompt}
    if model:
        params["model"] = model
    return DeepLink(action="prompt", params=params).to_url()


def create_session_link(session_id: str) -> str:
    """Create a deep link that resumes a session."""
    return DeepLink(action="resume", params={"session_id": session_id}).to_url()

"""rename — ``/rename`` rename the current conversation (port of TS local-jsx).

Port of ``typescript/src/commands/rename/`` (``rename.ts`` + ``generateSessionName.ts``).
``/rename <name>`` sets the name directly; bare ``/rename`` generates a short
kebab-case name from the conversation (Haiku side-call, best-effort).

**Persist channel (now genuinely live):** ``SessionStorage`` metadata ``title`` — the
session-persistence producer (``services/session_persistence.SessionPersister``,
driven by ``agent_bridge``) writes metadata + transcripts in normal TUI operation,
and the resume screen lists sessions labeled by ``meta.title`` first. The id channel
is unified by construction (``Session.create`` reads bootstrap ``get_session_id()``,
guarded by ``test_session_id_unified_with_bootstrap``), so this command provably
targets the same session directory the producer writes. When metadata is absent
(e.g. headless surfaces where no producer ran) the command falls back to
``init_metadata(title=…)`` — the session then appears in the resume list.

Output-style pattern: ``run()`` never touches ``ctx.ui`` (TS has no picker) → works
headless on every surface.

Deliberate divergences (documented for parity review):
  * **DROPPED:** the teammate guard (no ``isTeammate`` analog), ``saveAgentName`` +
    AppState ``standaloneAgentContext.name`` (no analog), the best-effort bridge
    title sync (only a protocol ``set_session_title`` exists — no concrete client),
    and the compact-boundary message filter (messages used as-is).
  * **Name generation** is anthropic-direct best-effort (the ``generate_llm_title``
    sibling pattern); ANY failure (no key/network/parse) → the no-context message
    (TS distinguishes only null-generation; merged — honest).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
)

# Verbatim TS generateSessionName.ts:22-24 system prompt.
_NAME_PROMPT = (
    "Generate a short kebab-case name (2-4 words) that captures the main topic of "
    'this conversation. Use lowercase words separated by hyphens. Examples: '
    '"fix-login-bug", "add-auth-feature", "refactor-api-client", '
    '"debug-test-failures". Return JSON with a "name" field.'
)

_NO_CONTEXT_MSG = (
    "Could not generate a name: no conversation context yet. Usage: /rename <name>"
)


def _conversation_text(messages: Any) -> str:
    """Flatten user/assistant texts (the TS extractConversationText reduction)."""
    parts: list[str] = []
    for msg in list(messages or [])[:20]:
        role = (
            msg.get("role") or msg.get("type")
            if isinstance(msg, Mapping)
            else getattr(msg, "role", None) or getattr(msg, "type", None)
        )
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content") if isinstance(msg, Mapping) else getattr(msg, "content", None)
        if isinstance(content, str) and content.strip():
            parts.append(f"{role}: {content.strip()}")
        elif isinstance(content, list):
            for block in content:
                text = (
                    block.get("text")
                    if isinstance(block, Mapping)
                    else getattr(block, "text", None)
                )
                if text and str(text).strip():
                    parts.append(f"{role}: {str(text).strip()}")
    return "\n".join(parts)


async def _generate_session_name(messages: Any) -> str | None:
    """Port of TS ``generateSessionName`` — kebab-case 2-4 words via a Haiku
    side-call; best-effort ``None`` on any failure (the ``generate_llm_title``
    sibling pattern)."""
    text = _conversation_text(messages)
    if not text:
        return None
    try:
        import anthropic

        from src.services.api.custom_headers import get_anthropic_custom_headers
        client = anthropic.Anthropic(
            default_headers=get_anthropic_custom_headers() or None
        )
        result = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=100,
            system=_NAME_PROMPT,
            messages=[{"role": "user", "content": text[:4000]}],
        )
        raw = "".join(
            getattr(b, "text", "") or "" for b in (result.content or [])
        ).strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        name = json.loads(m.group(0)).get("name")
        return name.strip() if isinstance(name, str) and name.strip() else None
    except Exception:
        return None


@dataclass(frozen=True)
class RenameCommand(InteractiveCommand):
    """Rename the current conversation (SessionStorage metadata title)."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        name = (args or "").strip()
        if not name:
            messages = getattr(context.conversation, "messages", None) or []
            generated = await _generate_session_name(messages)
            if not generated:
                return InteractiveOutcome(message=_NO_CONTEXT_MSG, display="system")
            name = generated

        from src.bootstrap.state import get_session_id
        from src.services.session_storage import SessionStorage

        storage = SessionStorage(session_id=str(get_session_id()))
        if storage.get_metadata() is None:
            storage.init_metadata(title=name)
        else:
            storage.update_metadata(title=name)
        # Verbatim TS rename.ts:85 (display:'system').
        return InteractiveOutcome(message=f"Session renamed to: {name}", display="system")


RENAME_COMMAND = RenameCommand(
    name="rename",
    description="Rename the current conversation",  # verbatim TS index.ts
)


__all__ = ["RENAME_COMMAND", "RenameCommand"]

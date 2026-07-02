"""ch06 round-4 PR-B — the auto-mode transcript classifier.

The LLM analog of TS ``classifyYoloAction`` (``utils/permissions/
yoloClassifier.ts``): a per-ask security classification over a serialized
conversation transcript, gated behind a settings knob (the
``feature('TRANSCRIPT_CLASSIFIER')`` analog, default OFF).

Cost discipline — the load-bearing design decision: the STATIC heuristic
(``_auto_mode_classify_static``) stays the fast-path pre-filter. A heuristic
``allow`` resolves with ZERO LLM call; the LLM fires ONLY on the residual
heuristic-``deny``. So enabling the flag adds LLM cost only where auto-mode
would otherwise have DENIED — never on what the heuristic already allows.

Accuracy note (critic M3): this is a COST-driven pre-filter, NOT a claim
that the static allow-set is a strict subset of TS's classifier-exempt set.
It is not: TS does NOT fast-path safe Bash (its allowlist has no Bash and
its acceptEdits fast-path only covers file edits), so TS would send a
safe-*named* Bash command to the classifier where the port fast-path-allows
it (the static bash classifier is largely name-based / arg-blind). The
port's fast-path therefore trusts a slightly wider set than TS. This is NOT
a regression — it exactly equals today's flag-OFF behavior (the static
heuristic already governs auto mode) — and the LLM only ADDS coverage on
the deny side. Accepted for cost; documented so the rationale is true.

Safety posture (TS parity):
* classifier error / timeout / abort → fail-CLOSED (deny) unless the
  ``auto_mode_iron_gate_open`` knob flips it (TS iron gate, default
  fail-closed);
* parse failure (no/invalid classifier output) → hard block (not
  iron-gated);
* denial-limit: 3 consecutive or 20 total denials → fall back to a manual
  prompt (interactive) or deny (headless).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# TS classifierShared / denialTracking constants.
MAX_CLASSIFIER_TRANSCRIPT_CHARS = 200_000
MAX_CLASSIFIER_BLOCK_VALUE_CHARS = 32_000
MAX_CONSECUTIVE_DENIALS = 3
MAX_TOTAL_DENIALS = 20

_CLASSIFIER_SYSTEM_PROMPT = (
    "You are a security classifier for an autonomous coding agent running "
    "in auto-approve mode. Given the recent conversation transcript and a "
    "pending tool action, decide whether the action should be BLOCKED "
    "because it is inconsistent with what the user asked for, or is "
    "dangerous, irreversible, exfiltrates data, installs persistence, or "
    "weakens security. Err on the side of blocking when uncertain. Only "
    "explicit user confirmation in the transcript overrides a block.\n\n"
    "Respond with a single JSON object and nothing else:\n"
    '{"should_block": true|false, "reason": "<one short sentence>"}'
)


@dataclass
class DenialState:
    """Global (per-session) denial tracking — TS DenialTrackingState."""

    consecutive_denials: int = 0
    total_denials: int = 0

    def record_success(self) -> None:
        self.consecutive_denials = 0

    def record_denial(self) -> None:
        self.consecutive_denials += 1
        self.total_denials += 1

    def should_fallback_to_prompt(self) -> bool:
        return (
            self.consecutive_denials >= MAX_CONSECUTIVE_DENIALS
            or self.total_denials >= MAX_TOTAL_DENIALS
        )


@dataclass
class ClassifierDecision:
    allow: bool
    reason: str
    # 'fast_path' | 'llm' | 'unavailable' | 'parse_error' | 'empty_action'
    source: str = "llm"
    # True when the classifier could not run (error/timeout) — iron-gated.
    unavailable: bool = False


def is_transcript_classifier_enabled() -> bool:
    """The ``feature('TRANSCRIPT_CLASSIFIER')`` analog (settings, default off)."""
    try:
        from src.settings.settings import get_settings

        return bool(getattr(get_settings(), "auto_mode_classifier_enabled", False))
    except Exception:  # noqa: BLE001 — a settings read failure keeps it off
        return False


def _iron_gate_open() -> bool:
    try:
        from src.settings.settings import get_settings

        return bool(getattr(get_settings(), "auto_mode_iron_gate_open", False))
    except Exception:  # noqa: BLE001
        return False


def _project_tool_input(tool: Any, tool_input: dict[str, Any]) -> Any:
    """Per-tool classifier projection (TS toAutoClassifierInput; default '')."""
    proj = getattr(tool, "to_auto_classifier_input", None)
    if proj is None:
        return ""
    try:
        return proj(tool_input)
    except Exception:  # noqa: BLE001
        return ""


def _truncate(value: str) -> str:
    if len(value) > MAX_CLASSIFIER_BLOCK_VALUE_CHARS:
        return value[:MAX_CLASSIFIER_BLOCK_VALUE_CHARS] + "…(truncated)"
    return value


def serialize_transcript_for_classifier(
    messages: list[Any],
    tool_lookup: Any,
    budget: int,
) -> str:
    """Newest-first serialization; user *text* + assistant *tool_use* only.

    Assistant text is deliberately DROPPED (TS yoloClassifier.ts:299-355):
    model-authored prose could be crafted to influence the classifier.
    """
    lines: list[str] = []
    used = 0
    for msg in reversed(messages or []):
        role = getattr(msg, "role", None) or (
            msg.get("role") if isinstance(msg, dict) else None
        )
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")

        if role == "user":
            text = content if isinstance(content, str) else _blocks_user_text(content)
            if text:
                entry = f"User: {_truncate(text)}"
                if used + len(entry) > budget:
                    break
                lines.append(entry)
                used += len(entry)
        elif role == "assistant" and isinstance(content, list):
            for block in content:
                btype = _block_type(block)
                if btype != "tool_use":
                    continue  # drop assistant text
                name = _block_field(block, "name") or "?"
                binput = _block_field(block, "input") or {}
                tool = _lookup_tool(tool_lookup, name)
                if tool is None:
                    # critic m1 — TS toCompactBlock drops blocks for
                    # unknown tools (yoloClassifier.ts:413-414) rather than
                    # serializing raw input, so a tool's projection can't be
                    # bypassed by an absent-registry lane.
                    continue
                projected = _project_tool_input(tool, binput)
                if projected == "":
                    continue
                entry = json.dumps({name: projected}, default=str)
                entry = _truncate(entry)
                if used + len(entry) > budget:
                    break
                lines.append(entry)
                used += len(entry)
    lines.reverse()  # back to chronological for the model
    return "\n".join(lines)


def _blocks_user_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if _block_type(b) == "text":
            parts.append(str(_block_field(b, "text") or ""))
    return "\n".join(p for p in parts if p)


def _block_type(block: Any) -> str:
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _block_field(block: Any, name: str) -> Any:
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _lookup_tool(tool_lookup: Any, name: str) -> Any:
    if tool_lookup is None:
        return None
    try:
        for t in tool_lookup:
            if getattr(t, "name", None) == name or name in (
                getattr(t, "aliases", None) or ()
            ):
                return t
    except TypeError:
        return None
    return None


def _resolve_classifier_provider(session_provider: Any) -> Any:
    """The classifier provider/model — TS getClassifierModel default = the
    main-loop model/provider. Optional settings override."""
    try:
        from src.settings.settings import get_settings

        s = get_settings()
        model = getattr(s, "auto_mode_classifier_model", "") or ""
        pname = getattr(s, "auto_mode_classifier_provider", "") or ""
    except Exception:  # noqa: BLE001
        model, pname = "", ""

    if pname:
        try:
            from src.config import get_provider_config
            from src.providers import get_provider_class, resolve_api_key

            cfg = get_provider_config(pname)
            provider = get_provider_class(pname)(
                api_key=resolve_api_key(pname, cfg),
                base_url=cfg.get("base_url"),
                model=model or cfg.get("default_model"),
            )
            return provider
        except Exception:  # noqa: BLE001 — fall back to the session provider
            logger.debug("classifier provider build failed; using session provider",
                         exc_info=True)
    if model and session_provider is not None:
        # Same provider, overridden model — clone shallowly if possible.
        try:
            import copy

            p = copy.copy(session_provider)
            p.model = model
            return p
        except Exception:  # noqa: BLE001
            pass
    return session_provider


def classify_action_llm(
    tool: Any,
    tool_input: dict[str, Any],
    tool_use_context: Any,
) -> ClassifierDecision:
    """Fire the LLM classifier for one pending action. Never raises."""
    # Empty projection → allow without the LLM (TS 1141-1147).
    projected = _project_tool_input(tool, tool_input)
    if projected == "":
        return ClassifierDecision(
            allow=True, reason="Tool declares no classifier-relevant input",
            source="empty_action",
        )

    provider = getattr(tool_use_context, "_active_provider", None) or getattr(
        tool_use_context, "provider", None
    )
    if provider is None:
        # No provider to classify with → iron gate.
        return _iron_gate(tool)

    messages = getattr(tool_use_context, "messages", None) or []
    tool_lookup = getattr(getattr(tool_use_context, "options", None), "tools", None)
    abort_signal = getattr(
        getattr(tool_use_context, "abort_controller", None), "signal", None
    )

    action_repr = json.dumps({getattr(tool, "name", "?"): projected}, default=str)
    budget = max(0, MAX_CLASSIFIER_TRANSCRIPT_CHARS - len(action_repr))
    transcript = serialize_transcript_for_classifier(messages, tool_lookup, budget)

    claude_md = ""
    try:
        from src.bootstrap.state import get_cached_claude_md_content

        claude_md = get_cached_claude_md_content() or ""
    except Exception:  # noqa: BLE001
        claude_md = ""

    user_body = ""
    if claude_md:
        user_body += f"<user_claude_md>\n{_truncate(claude_md)}\n</user_claude_md>\n\n"
    if transcript:
        user_body += f"<transcript>\n{transcript}\n</transcript>\n\n"
    user_body += f"<pending_action>\n{action_repr}\n</pending_action>"

    classifier_provider = _resolve_classifier_provider(provider)
    try:
        response = classifier_provider.chat_stream_response(
            [{"role": "user", "content": user_body}],
            on_text_chunk=None,
            abort_signal=abort_signal,
            system=_CLASSIFIER_SYSTEM_PROMPT,
            max_tokens=512,
        )
    except (NotImplementedError, AttributeError):
        try:
            response = classifier_provider.chat(
                [{"role": "user", "content": user_body}],
                system=_CLASSIFIER_SYSTEM_PROMPT,
                max_tokens=512,
            )
        except Exception:  # noqa: BLE001 — classifier unavailable → iron gate
            logger.debug("classifier call failed (chat fallback)", exc_info=True)
            return _iron_gate(tool)
    except Exception:  # noqa: BLE001 — classifier unavailable → iron gate
        logger.debug("classifier call failed", exc_info=True)
        return _iron_gate(tool)

    text = getattr(response, "content", None) or ""
    return _parse_classifier_output(text, tool)


def _parse_classifier_output(text: Any, tool: Any) -> ClassifierDecision:
    if not isinstance(text, str) or not text.strip():
        # Parse failure → hard block (NOT iron-gated; TS treats unavailable
        # and parse-failure differently).
        return ClassifierDecision(
            allow=False, reason="Classifier returned no parseable output",
            source="parse_error",
        )
    # Extract the first JSON object.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ClassifierDecision(
            allow=False, reason="Classifier response was not JSON",
            source="parse_error",
        )
    try:
        obj = json.loads(text[start : end + 1])
    except Exception:  # noqa: BLE001
        return ClassifierDecision(
            allow=False, reason="Classifier response was not valid JSON",
            source="parse_error",
        )
    should_block = bool(obj.get("should_block", True))
    reason = str(obj.get("reason", "")) or (
        "blocked by classifier" if should_block else "approved by classifier"
    )
    return ClassifierDecision(allow=not should_block, reason=reason, source="llm")


def _iron_gate(tool: Any) -> ClassifierDecision:
    """Classifier unavailable (error/timeout). Default fail-CLOSED."""
    if _iron_gate_open():
        # Fail-open: caller treats an unavailable+allow as "return the ask".
        return ClassifierDecision(
            allow=True, reason="Classifier unavailable (iron gate open)",
            source="unavailable", unavailable=True,
        )
    return ClassifierDecision(
        allow=False,
        reason=(
            f"Auto-mode classifier is unavailable and could not verify "
            f"{getattr(tool, 'name', 'this tool')}"
        ),
        source="unavailable", unavailable=True,
    )

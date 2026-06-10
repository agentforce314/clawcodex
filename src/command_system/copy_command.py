"""copy — ``/copy`` copy-assistant-response command (port of TS local-jsx).

Port of ``typescript/src/commands/copy/`` (``copy.tsx`` + ``index.ts``). Copies the
latest (or Nth-latest, ``/copy N``) assistant response — or one of its fenced code
blocks via a picker — to the clipboard, always also writing a temp-file fallback.

**Headless keystone:** the *direct* path (no code blocks in the message, or the
``copyFullResponse`` config opt-out, including via ``/copy N``) never touches
``ctx.ui`` → works on every surface incl. ``NullUIHost``. Only the code-block picker
needs a surface. Coexistence: **fall-through** (no TUI dialog for ``/copy``).

Deliberate divergences (documented for parity review):
  * **Clipboard via subprocess** (pbcopy / xclip / xsel / clip), not TS's OSC 52
    escape (which is fire-and-forget and therefore always *claims* success). A failed
    subprocess copy must not lie: clipboard-fail + file-ok reports
    ``"Written to {path} (…)"`` instead of "Copied to clipboard".
  * **Line-based fence parser**, not the marked lexer — indented code blocks and
    ``~~~`` fences are not recognized (fenced ``` blocks are what assistants emit).
  * **``COPY_DIR`` = ``{tmpdir}/clawcodex``** (TS uses ``{tmpdir}/claude``).
  * **Codepoint-based label truncation** (TS uses display-width ``stringWidth``).
  * **No ``isApiErrorMessage`` flag** on Python messages — empty texts are skipped,
    which is the practical effect.
  * **``w`` write-only keyboard shortcut dropped** (no keyboard primitive on the
    ``select`` bridge).
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
    UIOption,
)

MAX_LOOKBACK = 20  # TS copy.tsx:25
RESPONSE_FILENAME = "response.md"

# Verbatim port of TS STRIPPED_TAGS_RE (messages.ts:2764-2765).
_STRIPPED_TAGS_RE = re.compile(
    r"<(commit_analysis|context|function_analysis|pr_analysis)>.*?</\1>\n?",
    re.DOTALL,
)


def _strip_prompt_xml_tags(content: str) -> str:
    """TS ``stripPromptXMLTags`` (messages.ts:2767-2769)."""
    return _STRIPPED_TAGS_RE.sub("", content).strip()


def _copy_dir() -> Path:
    return Path(tempfile.gettempdir()) / "clawcodex"


@dataclass(frozen=True)
class CodeBlock:
    code: str
    lang: str | None


def _extract_code_blocks(markdown: str) -> list[CodeBlock]:
    """Fenced ``` code blocks from the (XML-stripped) markdown. Line-based parser
    (see module divergences): an opening ``` captures the language token; lines are
    collected until the closing ```; an unclosed fence yields no block."""
    blocks: list[CodeBlock] = []
    in_fence = False
    lang: str | None = None
    buf: list[str] = []
    for line in _strip_prompt_xml_tags(markdown).split("\n"):
        stripped = line.strip()
        if not in_fence and stripped.startswith("```"):
            in_fence = True
            token = stripped[3:].strip()
            lang = token.split()[0] if token else None
            buf = []
            continue
        if in_fence and stripped.startswith("```") and not stripped[3:].strip():
            # CommonMark: a CLOSING fence may not carry an info string — a ```js
            # line inside an open fence is content, not a close.
            blocks.append(CodeBlock(code="\n".join(buf), lang=lang))
            in_fence = False
            lang = None
            buf = []
            continue
        if in_fence:
            buf.append(line)
    return blocks


def _message_role(msg: Any) -> Any:
    if isinstance(msg, Mapping):
        return msg.get("role") or msg.get("type")
    return getattr(msg, "role", None) or getattr(msg, "type", None)


def _message_text(msg: Any) -> str:
    """Assistant text joined with '\\n\\n' (TS extractTextContent(content, '\\n\\n'))."""
    from src.utils.export_renderer import extract_message_content

    content = extract_message_content(msg)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping):
                if block.get("type") in (None, "text") and block.get("text"):
                    parts.append(str(block["text"]))
            else:
                text = getattr(block, "text", None)
                if text and getattr(block, "type", "text") in (None, "text"):
                    parts.append(str(text))
        return "\n\n".join(parts).strip()
    return ""


def collect_recent_assistant_texts(messages: Any) -> list[str]:
    """TS ``collectRecentAssistantTexts`` (copy.tsx:50-61): newest-first assistant
    texts that actually said something, capped at ``MAX_LOOKBACK``."""
    texts: list[str] = []
    for msg in reversed(list(messages or [])):
        if len(texts) >= MAX_LOOKBACK:
            break
        if _message_role(msg) != "assistant":
            continue
        text = _message_text(msg)
        if text:
            texts.append(text)
    return texts


def _file_extension(lang: str | None) -> str:
    """TS ``fileExtension`` (copy.tsx:62-72) — sanitized to prevent path traversal."""
    if lang:
        sanitized = re.sub(r"[^a-zA-Z0-9]", "", lang)
        if sanitized and sanitized != "plaintext":
            return f".{sanitized}"
    return ".txt"


def _truncate_line(text: str, max_len: int) -> str:
    """First line, codepoint-truncated to ``max_len`` with ``…`` (TS truncateLine,
    minus display-width awareness — module divergences)."""
    first = text.split("\n")[0]
    if len(first) <= max_len:
        return first
    return first[: max_len - 1] + "…"


def _set_clipboard(text: str) -> bool:
    """Best-effort OS clipboard via subprocess; True on success."""
    if sys.platform == "darwin":
        candidates = [["pbcopy"]]
    elif sys.platform.startswith("win"):
        candidates = [["clip"]]
    else:
        candidates = [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for cmd in candidates:
        try:
            proc = subprocess.run(
                cmd, input=text.encode("utf-8"), capture_output=True, timeout=5
            )
            if proc.returncode == 0:
                return True
        except Exception:
            continue
    return False


def _write_to_file(text: str, filename: str) -> str:
    copy_dir = _copy_dir()
    copy_dir.mkdir(parents=True, exist_ok=True)
    path = copy_dir / filename
    path.write_text(text, encoding="utf-8")
    return str(path)


def _copy_or_write(text: str, filename: str) -> str:
    """TS ``copyOrWriteToFile`` (copy.tsx:81-94), with the honest clipboard-failure
    divergence (module docstring). ``char_count`` is Python codepoints; TS
    ``text.length`` is UTF-16 code units (astral chars differ by one)."""
    copied = _set_clipboard(text)
    line_count = text.count("\n") + 1
    char_count = len(text)
    try:
        path = _write_to_file(text, filename)
    except Exception as exc:
        if copied:
            return f"Copied to clipboard ({char_count} characters, {line_count} lines)"
        return f"Failed to copy: {exc}"
    if copied:
        return (
            f"Copied to clipboard ({char_count} characters, {line_count} lines)\n"
            f"Also written to {path}"
        )
    return f"Written to {path} ({char_count} characters, {line_count} lines)"


def _copy_full_response_enabled() -> bool:
    from src.config import load_config

    return bool(load_config().get("copyFullResponse"))


def _persist_copy_full_response() -> None:
    from src.config import _get_default_manager, load_config

    if not load_config().get("copyFullResponse"):
        _get_default_manager().set_global("copyFullResponse", True)


@dataclass(frozen=True)
class CopyCommand(InteractiveCommand):
    """Copy the latest assistant response (or a code block) to the clipboard."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        messages = getattr(context.conversation, "messages", None) or []
        texts = collect_recent_assistant_texts(messages)
        if not texts:
            return InteractiveOutcome(message="No assistant message to copy", display="user")

        # /copy N reaches back N-1 messages (TS copy.tsx:341-355).
        age = 0
        arg = (args or "").strip()
        if arg:
            # int(arg) mirrors TS Number()+isInteger: accepts "+2"/"02", rejects
            # floats and non-numerics.
            try:
                n = int(arg)
                is_int = True
            except ValueError:
                n, is_int = 0, False
            if not is_int or n < 1:
                return InteractiveOutcome(
                    message=(
                        "Usage: /copy [N] where N is 1 (latest), 2, 3, … "
                        f"Got: {arg}"
                    ),
                    display="user",
                )
            if n > len(texts):
                noun = "message" if len(texts) == 1 else "messages"
                return InteractiveOutcome(
                    message=f"Only {len(texts)} assistant {noun} available to copy",
                    display="user",
                )
            age = n - 1

        text = texts[age]
        code_blocks = _extract_code_blocks(text)

        if not code_blocks or _copy_full_response_enabled():
            return InteractiveOutcome(
                message=_copy_or_write(text, RESPONSE_FILENAME), display="user"
            )

        return await self._pick(context, text, code_blocks)

    async def _pick(
        self, context: CommandContext, full_text: str, code_blocks: list[CodeBlock]
    ) -> InteractiveOutcome:
        options: list[UIOption] = [
            UIOption(
                value="full",
                label="Full response",
                description=(
                    f"{len(full_text)} chars, {full_text.count(chr(10)) + 1} lines"
                ),
            )
        ]
        for i, block in enumerate(code_blocks):
            lines = block.code.count("\n") + 1
            desc_parts = [p for p in (block.lang, f"{lines} lines" if lines > 1 else None) if p]
            options.append(
                UIOption(
                    value=str(i),
                    label=_truncate_line(block.code, 60),
                    description=", ".join(desc_parts) or None,
                )
            )
        options.append(
            UIOption(
                value="always",
                label="Always copy full response",
                description="Skip this picker in the future (revert via /config)",
            )
        )

        picked = await context.ui.select("Select content to copy:", options)
        if picked is None:
            return InteractiveOutcome(message="Copy cancelled", display="system")

        if picked in ("full", "always"):
            result = _copy_or_write(full_text, RESPONSE_FILENAME)
            if picked == "always":
                _persist_copy_full_response()
                result += "\nPreference saved. Use /config to change copyFullResponse"
            return InteractiveOutcome(message=result, display="user")

        block = code_blocks[int(picked)]
        result = _copy_or_write(block.code, f"copy{_file_extension(block.lang)}")
        return InteractiveOutcome(message=result, display="user")


COPY_COMMAND = CopyCommand(
    name="copy",
    # Verbatim TS index.ts.
    description="Copy Claude's last response to clipboard (or /copy N for the Nth-latest)",
)


__all__ = ["COPY_COMMAND", "CopyCommand", "collect_recent_assistant_texts"]

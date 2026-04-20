"""
Layer 1: Tool-result budget — persist large tool results to disk.

The cheapest compression layer.  Tool results exceeding a token threshold
are written to a session-local directory and replaced in-message with a
short reference string.  This is pure I/O with no LLM call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...types.content_blocks import ContentBlock, TextBlock, ToolResultBlock
from ...types.messages import Message, UserMessage
from ...token_estimation import count_tokens

logger = logging.getLogger(__name__)

# Threshold in estimated tokens — results above this are offloaded
DEFAULT_MAX_RESULT_TOKENS = 8_000

# Reference marker template
STORED_REFERENCE_TEMPLATE = "[Tool result stored at: {path}]"

# Manifest file name inside the budget directory
MANIFEST_FILENAME = "budget_manifest.json"


@dataclass
class StoredResult:
    """Record of a single offloaded tool result."""
    tool_use_id: str
    path: str
    original_tokens: int


@dataclass
class BudgetManifest:
    """Tracks all offloaded tool results for the session."""
    stored: list[StoredResult] = field(default_factory=list)

    def save(self, budget_dir: Path) -> None:
        manifest_path = budget_dir / MANIFEST_FILENAME
        data = [
            {"tool_use_id": s.tool_use_id, "path": s.path, "original_tokens": s.original_tokens}
            for s in self.stored
        ]
        manifest_path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, budget_dir: Path) -> BudgetManifest:
        manifest_path = budget_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            return cls()
        try:
            data = json.loads(manifest_path.read_text())
            return cls(
                stored=[
                    StoredResult(
                        tool_use_id=item["tool_use_id"],
                        path=item["path"],
                        original_tokens=item["original_tokens"],
                    )
                    for item in data
                ]
            )
        except Exception:
            logger.warning("Failed to load budget manifest, starting fresh")
            return cls()


def _estimate_block_tokens(block: ContentBlock) -> int:
    """Estimate token count for a content block."""
    if isinstance(block, ToolResultBlock):
        content = block.content
        if isinstance(content, str):
            return count_tokens(content)
        if isinstance(content, list):
            total = 0
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        total += count_tokens(item.get("text", ""))
                    elif item.get("type") in ("image", "document"):
                        total += 2000  # rough estimate for media
                elif isinstance(item, TextBlock):
                    total += count_tokens(item.text)
            return total
    if isinstance(block, TextBlock):
        return count_tokens(block.text)
    if isinstance(block, dict):
        if block.get("type") == "tool_result":
            c = block.get("content", "")
            if isinstance(c, str):
                return count_tokens(c)
            return count_tokens(str(c))
    return 0


def _content_to_string(content: str | list[Any]) -> str:
    """Serialise tool-result content to a string for disk storage."""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def apply_tool_result_budget(
    messages: list[Message],
    budget_dir: Path | str | None = None,
    max_result_tokens: int = DEFAULT_MAX_RESULT_TOKENS,
) -> tuple[list[Message], int]:
    """
    Persist large tool results to disk and replace with references.

    Args:
        messages: Conversation messages (typed ``Message`` objects).
        budget_dir: Directory for stored results.  If ``None``, a temp
            directory under ``/tmp/claw_codex_budget/`` is used.
        max_result_tokens: Results above this threshold are offloaded.

    Returns:
        ``(modified_messages, tokens_saved)``
    """
    if budget_dir is None:
        budget_dir = Path("/tmp/claw_codex_budget") / str(os.getpid())
    budget_dir = Path(budget_dir)
    budget_dir.mkdir(parents=True, exist_ok=True)

    manifest = BudgetManifest.load(budget_dir)
    already_stored: set[str] = {s.tool_use_id for s in manifest.stored}
    tokens_saved = 0
    result_messages: list[Message] = []

    for msg in messages:
        if msg.role != "user" or not isinstance(msg.content, list):
            result_messages.append(msg)
            continue

        new_content: list[ContentBlock] = []
        changed = False

        for block in msg.content:
            # Handle typed ToolResultBlock
            if isinstance(block, ToolResultBlock):
                if block.tool_use_id in already_stored:
                    new_content.append(block)
                    continue
                est = _estimate_block_tokens(block)
                if est > max_result_tokens:
                    # Offload to disk
                    content_str = _content_to_string(block.content)
                    file_hash = hashlib.sha256(
                        block.tool_use_id.encode()
                    ).hexdigest()[:12]
                    fname = f"result_{file_hash}.txt"
                    fpath = budget_dir / fname
                    fpath.write_text(content_str, encoding="utf-8")

                    ref = STORED_REFERENCE_TEMPLATE.format(path=str(fpath))
                    new_block = ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=ref,
                        is_error=block.is_error,
                    )
                    new_content.append(new_block)
                    changed = True
                    tokens_saved += est - count_tokens(ref)

                    manifest.stored.append(StoredResult(
                        tool_use_id=block.tool_use_id,
                        path=str(fpath),
                        original_tokens=est,
                    ))
                    already_stored.add(block.tool_use_id)
                else:
                    new_content.append(block)

            # Handle raw dict tool_result (backward compat)
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                tuid = block.get("tool_use_id", "")
                if tuid in already_stored:
                    new_content.append(block)
                    continue
                est = _estimate_block_tokens(block)
                if est > max_result_tokens:
                    content_str = _content_to_string(block.get("content", ""))
                    file_hash = hashlib.sha256(tuid.encode()).hexdigest()[:12]
                    fname = f"result_{file_hash}.txt"
                    fpath = budget_dir / fname
                    fpath.write_text(content_str, encoding="utf-8")

                    ref = STORED_REFERENCE_TEMPLATE.format(path=str(fpath))
                    new_block = {**block, "content": ref}
                    new_content.append(new_block)
                    changed = True
                    tokens_saved += est - count_tokens(ref)

                    manifest.stored.append(StoredResult(
                        tool_use_id=tuid,
                        path=str(fpath),
                        original_tokens=est,
                    ))
                    already_stored.add(tuid)
                else:
                    new_content.append(block)
            else:
                new_content.append(block)

        if changed:
            new_msg = UserMessage(
                content=new_content,
                uuid=msg.uuid,
                timestamp=msg.timestamp,
                isMeta=msg.isMeta,
            )
            result_messages.append(new_msg)
        else:
            result_messages.append(msg)

    if tokens_saved > 0:
        manifest.save(budget_dir)

    return result_messages, tokens_saved


def cleanup_budget_dir(budget_dir: Path | str) -> None:
    """Remove all stored tool results and the manifest."""
    budget_dir = Path(budget_dir)
    if not budget_dir.exists():
        return
    for f in budget_dir.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
    try:
        budget_dir.rmdir()
    except OSError:
        pass

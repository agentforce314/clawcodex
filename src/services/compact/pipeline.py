"""
Compression pipeline — orchestrates the 5 compression layers in order.

Runs cheap → expensive.  If earlier layers free enough tokens, later
layers are no-ops.

Layers:
  1. apply_tool_result_budget  — Persist large results to disk
  2. snip_compact              — Trim old tool results
  3. microcompact              — Compress intermediate tool calls
  4. context_collapse          — Read-time projection
  5. autocompact               — Full LLM summarization (last resort)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...types.messages import Message
from ...providers.base import BaseProvider

from .tool_result_budget import apply_tool_result_budget
from .snip_compact import snip_compact
from .context_collapse import ContextCollapseStore, get_context_collapse_state
from .autocompact import (
    AutoCompactTracking,
    auto_compact_if_needed,
    should_auto_compact,
)
from ...context_system.microcompact import (
    microcompact_typed_messages,
    TimeBasedMCConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class CompressionResult:
    """Result of running the compression pipeline."""
    messages: list[Message]
    tokens_saved: int = 0
    layers_applied: list[str] = field(default_factory=list)
    autocompact_result: Any | None = None  # CompactionResult if layer 5 ran


@dataclass
class PipelineConfig:
    """Configuration for the compression pipeline."""
    # Layer 1: tool result budget
    budget_dir: Path | str | None = None
    max_result_tokens: int = 8_000

    # Layer 2: snip compact
    snip_keep_recent: int = 10

    # Layer 3: microcompact
    # TS time-based MC is disabled by default (GrowthBook enabled: false),
    # cached MC uses API cache_edits (no local mutation), and legacy MC was
    # removed.  So microcompact is effectively a no-op on the main thread.
    mc_enabled: bool = False
    mc_keep_recent: int = 5
    mc_time_config: TimeBasedMCConfig | None = None

    # Layer 4: context collapse
    collapse_store: ContextCollapseStore | None = None

    # Layer 5: autocompact
    context_window: int = 200_000
    autocompact_threshold: float = 0.80
    autocompact_tracking: AutoCompactTracking | None = None

    # Layer 5: post-compact attachment context
    # Forwarded into auto_compact_if_needed → CompactContext so post-compact
    # file/plan restoration fires on auto-compact, not just /compact.
    read_file_state: dict[str, Any] | None = None
    plan_file_path: str | None = None
    memory_paths: set[str] | None = None

    # Global
    provider: BaseProvider | None = None
    model: str = ""
    custom_instructions: str | None = None

    # Token budget: if pipeline frees this many tokens, skip remaining layers
    early_exit_tokens: int = 20_000


class CompressionPipeline:
    """
    Orchestrates the 5-layer compression pipeline.

    Usage::

        pipeline = CompressionPipeline(config)
        result = await pipeline.run(messages, input_token_count)
    """

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self._config = config or PipelineConfig()

    async def run(
        self,
        messages: list[Message],
        input_token_count: int = 0,
    ) -> CompressionResult:
        """
        Run all compression layers in order, short-circuiting if enough
        tokens are freed by early layers.

        Args:
            messages: Current conversation messages.
            input_token_count: Estimated input token count (for autocompact decision).

        Returns:
            ``CompressionResult`` with the (potentially modified) messages,
            total tokens saved, and which layers were applied.
        """
        cfg = self._config
        total_saved = 0
        layers_applied: list[str] = []
        current_messages = messages

        # --- Layer 1: Tool Result Budget ---
        try:
            current_messages, saved = apply_tool_result_budget(
                current_messages,
                budget_dir=cfg.budget_dir,
                max_result_tokens=cfg.max_result_tokens,
            )
            if saved > 0:
                total_saved += saved
                layers_applied.append("tool_result_budget")
                logger.debug("Layer 1 (tool_result_budget): saved %d tokens", saved)
                if total_saved >= cfg.early_exit_tokens:
                    return CompressionResult(
                        messages=current_messages,
                        tokens_saved=total_saved,
                        layers_applied=layers_applied,
                    )
        except Exception:
            logger.warning("Layer 1 (tool_result_budget) failed", exc_info=True)

        # --- Layer 2: Snip Compact ---
        try:
            current_messages, saved = snip_compact(
                current_messages,
                keep_recent=cfg.snip_keep_recent,
            )
            if saved > 0:
                total_saved += saved
                layers_applied.append("snip_compact")
                logger.debug("Layer 2 (snip_compact): saved %d tokens", saved)
                if total_saved >= cfg.early_exit_tokens:
                    return CompressionResult(
                        messages=current_messages,
                        tokens_saved=total_saved,
                        layers_applied=layers_applied,
                    )
        except Exception:
            logger.warning("Layer 2 (snip_compact) failed", exc_info=True)

        # --- Layer 3: Microcompact ---
        # Gated by mc_enabled (default False) to match TS where microcompact
        # is a no-op on the main thread (time-based disabled, cached MC uses
        # API cache_edits, legacy removed).  Clearing tool results locally
        # breaks file_unchanged (model told to "refer to earlier content"
        # that microcompact already erased → falls back to Bash cat).
        if cfg.mc_enabled:
            try:
                current_messages, saved = microcompact_typed_messages(
                    current_messages,
                    keep_recent=cfg.mc_keep_recent,
                    time_config=cfg.mc_time_config,
                    force=True,
                )
                if saved > 0:
                    total_saved += saved
                    layers_applied.append("microcompact")
                    logger.debug("Layer 3 (microcompact): saved %d tokens", saved)
                    if total_saved >= cfg.early_exit_tokens:
                        return CompressionResult(
                            messages=current_messages,
                            tokens_saved=total_saved,
                            layers_applied=layers_applied,
                        )
            except Exception:
                logger.warning("Layer 3 (microcompact) failed", exc_info=True)

        # --- Layer 4: Context Collapse ---
        try:
            store = cfg.collapse_store or get_context_collapse_state()
            if store is not None and store.enabled and store.commits:
                current_messages = store.project_view(current_messages)
                layers_applied.append("context_collapse")
                logger.debug("Layer 4 (context_collapse): projected %d commits", len(store.commits))
        except Exception:
            logger.warning("Layer 4 (context_collapse) failed", exc_info=True)

        # --- Layer 5: Autocompact ---
        autocompact_result = None
        if cfg.provider is not None and cfg.model:
            try:
                result = await auto_compact_if_needed(
                    current_messages,
                    input_token_count - total_saved,
                    cfg.context_window,
                    cfg.provider,
                    cfg.model,
                    threshold_fraction=cfg.autocompact_threshold,
                    tracking=cfg.autocompact_tracking,
                    custom_instructions=cfg.custom_instructions,
                    read_file_state=cfg.read_file_state,
                    plan_file_path=cfg.plan_file_path,
                    memory_paths=cfg.memory_paths,
                )
                if result is not None:
                    total_saved += result.tokens_saved
                    layers_applied.append("autocompact")
                    autocompact_result = result
                    logger.debug("Layer 5 (autocompact): saved %d tokens", result.tokens_saved)
            except Exception:
                logger.warning("Layer 5 (autocompact) failed", exc_info=True)

        return CompressionResult(
            messages=current_messages,
            tokens_saved=total_saved,
            layers_applied=layers_applied,
            autocompact_result=autocompact_result,
        )


async def run_compression_pipeline(
    messages: list[Message],
    input_token_count: int = 0,
    config: PipelineConfig | None = None,
) -> CompressionResult:
    """
    Convenience function: run the full compression pipeline.

    Args:
        messages: Current conversation messages.
        input_token_count: Estimated input token count.
        config: Pipeline configuration.

    Returns:
        ``CompressionResult``
    """
    pipeline = CompressionPipeline(config)
    return await pipeline.run(messages, input_token_count)

---
title: "Source: Context Management (Ch.04b)"
type: source
created: 2026-04-08
updated: 2026-04-08
sources: [claude-code-sourcemap-learning-notebook/en/04b_context_management.md]
tags: [claude-code, context-management, compression, token-budget, caching]
---

# Context Management Deep Dive

**Source**: `raw/claude-code-sourcemap-learning-notebook/en/04b_context_management.md`

## Summary

Chapter 4b provides the complete deep dive into [[context-management]] — the 5-layer [[compression-pipeline]] from cheapest to most expensive, message lifecycle management, prompt caching optimization, token counting, and context window utilization. It details how [[claude-code]] fights the context limit: tool result budgets (30K chars default, Infinity for FileReadTool), microcompact (summaryPrefix replacement), context collapse (read-time projection that never mutates the REPL array), and autocompact (full LLM-based compression using 9 structured sections). Also covers `ContextCollapseBoundary` as a dividing line between compressed history and live context.

## Key Claims

- 5-layer compression pipeline ordered by cost: toolResultBudget (free) → snip (free) → microcompact (free) → contextCollapse (cheap) → autocompact (expensive LLM call)
- `toolResultBudget` defaults to 30K chars; `FileReadTool` overrides to Infinity to avoid read→truncate→read loops
- Microcompact replaces large tool results with their `summaryPrefix` — reversible and zero-cost
- Context collapse stores summaries in `collapseData` — original REPL messages untouched
- `ContextCollapseBoundary` marker divides compressed history from live, cacheable context
- Autocompact prompt requires 9 sections including "All user messages" (prevents losing user feedback) and "direct quotes" (prevents information drift)
- Token counting: `roughTokenCountEstimation()` uses chars/3.25 heuristic; `countTokens()` calls the API for precise counts
- Prompt cache: static system prompt sections get `scope: 'global'` cache, dynamic sections placed after `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`

## Entities Mentioned

- [[claude-code]]

## Concepts Mentioned

- [[context-management]]
- [[compression-pipeline]]
- [[prompt-cache]]
- [[token-budget]]
- [[autocompact]]

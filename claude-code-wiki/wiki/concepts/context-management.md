---
title: Context Management
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [04_query_loop_api.md, 04b_context_management.md, 07_prompt_engineering.md]
tags: [architecture, context-window, compression, core-system]
---

# Context Management

The system in [[claude-code]] that manages the limited context window — ensuring conversations can continue indefinitely despite fixed token limits.

## Definition

Context management encompasses the [[compression-pipeline]] (5 layers from cheapest to most expensive), [[prompt-cache]] optimization (static/dynamic boundary), token counting, and the illusion of "infinite" context achieved through automatic compression.

## How It Appears Across Sources

- [[04-query-loop-api]]: compression triggers, token budget, diminishing returns detection
- [[04b-context-management]]: full 5-layer pipeline deep dive, `ContextCollapseBoundary`, message lifecycle
- [[07-prompt-engineering]]: system prompt tells Claude context is "infinite"; compact prompt requires 9 structured sections

## Key Design Decisions

- **Cheapest first**: toolResultBudget (free) → snip (free) → microcompact (free) → contextCollapse (cheap) → [[autocompact]] (expensive LLM call)
- **Never mutate REPL array**: context collapse stores summaries in `collapseData`, keeping originals intact
- **`ContextCollapseBoundary`**: divides compressed history from live context — live part is cacheable
- **Transcript backup**: full conversation saved to disk, referenced after compression for detail retrieval

## Related Concepts

- [[compression-pipeline]]
- [[prompt-cache]]
- [[token-budget]]
- [[autocompact]]
- [[session-memory]]
- [[query-loop]]

## Open Questions

- What is the empirical compression ratio of autocompact across different conversation types?
- How does context collapse interact with fork mode's cache sharing?

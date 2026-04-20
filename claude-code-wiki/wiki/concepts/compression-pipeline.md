---
title: Compression Pipeline
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [04_query_loop_api.md, 04b_context_management.md]
tags: [architecture, context-window, optimization]
---

# Compression Pipeline

The 5-layer strategy in [[claude-code]] for managing growing context, ordered from cheapest to most expensive.

## Definition

| Layer | Name | Cost | Mechanism |
|-------|------|------|-----------|
| 1 | toolResultBudget | Free | Truncate tool results exceeding `maxResultSizeChars` (default 30K) |
| 2 | snip | Free | Replace fully consumed tool results with placeholder |
| 3 | microcompact | Free | Replace tool results with `summaryPrefix` |
| 4 | contextCollapse | Cheap | Summarize old messages (stored in `collapseData`, originals untouched) |
| 5 | [[autocompact]] | Expensive | Full LLM-based compression with 9-section structured template |

## Key Design Decisions

- **Layered degradation**: try cheapest approaches first, escalate only when necessary
- **FileReadTool exception**: `maxResultSizeChars: Infinity` to avoid read→truncate→read infinite loops
- **Context collapse is read-time projection**: REPL array never modified, summaries stored separately
- **Autocompact uses `maxTurns: 1`**: only one chance — if Claude calls tools instead of summarizing, compression fails

## Related Concepts

- [[context-management]]
- [[autocompact]]
- [[token-budget]]
- [[prompt-cache]]

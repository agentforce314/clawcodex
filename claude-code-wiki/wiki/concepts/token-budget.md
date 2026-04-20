---
title: Token Budget
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [04_query_loop_api.md, 04b_context_management.md]
tags: [context-window, optimization, cost]
---

# Token Budget

The system in [[claude-code]] that manages output token allocation and detects diminishing returns.

## Definition

Token budget governs how many tokens Claude is allowed to generate per turn and when to stop continuing. Key mechanism: if Claude has continued 3+ times with <500 token deltas between continuations, it detects diminishing returns and stops — preventing infinite loops of tiny outputs.

## How It Appears Across Sources

- [[04-query-loop-api]]: diminishing returns detection, max_output_tokens 3-stage recovery
- [[04b-context-management]]: token counting heuristics (`chars/3.25`), API-based precise counting

## Related Concepts

- [[context-management]]
- [[compression-pipeline]]
- [[query-loop]]

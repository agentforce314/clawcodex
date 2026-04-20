---
title: "Source: Query Loop & API Interaction (Ch.04)"
type: source
created: 2026-04-08
updated: 2026-04-08
sources: [claude-code-sourcemap-learning-notebook/en/04_query_loop_api.md]
tags: [claude-code, query-loop, streaming, concurrency, compression, design-patterns]
---

# Query Loop & API Interaction (In-Depth)

**Source**: `raw/claude-code-sourcemap-learning-notebook/en/04_query_loop_api.md`

## Summary

Chapter 4 is the heart of Claude Code. It covers `query.ts` (67KB, 1730 lines) — the `while(true)` + State object state machine design, the [[streaming-tool-executor]] with read-write lock concurrency and sibling abort, the 5-layer [[compression-pipeline]], hierarchical error recovery (max_output_tokens 3-stage, prompt_too_long 3-stage), [[prompt-cache]] full-chain optimization, [[token-budget]] with diminishing returns detection, message queue and interrupt injection, stop hooks, two tool orchestration modes, and QueryConfig immutable snapshot. Extracts 6 [[transferable-design-patterns]].

## Key Claims

- `while(true)` + State beats recursion: constant memory, no stack depth limit, precise state reset at continue sites
- `transition` field prevents recovery death loops — records WHY the last iteration continued
- [[streaming-tool-executor]] uses read-write lock semantics: ConcurrencySafe = read lock (parallel), non-safe = write lock (exclusive)
- 3-tier AbortController: user Ctrl+C → all stop; Bash error → siblings stop; permission denial → bubbles up
- 5-layer compression: toolResultBudget → snip → microcompact → contextCollapse → autocompact (cheap → expensive)
- Context collapse is "read-time projection" — REPL array never modified, summaries stored separately
- Withhold mode: recoverable errors are detained, not yielded, giving recovery a chance
- Token budget detects diminishing returns: 3+ continuations with <500 token deltas → stop
- 11 engineering ingenuities documented with real production bug stories

## Key Design Patterns (Transferable)

1. **Optimistic Recovery** — withhold errors, attempt recovery, expose only on failure
2. **Layered Degradation** — cheap solutions first, expensive last
3. **State Machine + Transition Log** — prevent recovery loops
4. **Read-Write Lock Concurrency** — reads parallel, writes exclusive
5. **Immutable Config Snapshot** — `(state, event, config) → newState`
6. **Hierarchical Cascade Cancellation** — abort reasons carry semantics

## Entities Mentioned

- [[claude-code]]
- [[streaming-tool-executor]]

## Concepts Mentioned

- [[query-loop]]
- [[compression-pipeline]]
- [[prompt-cache]]
- [[token-budget]]
- [[transferable-design-patterns]]
- [[context-management]]

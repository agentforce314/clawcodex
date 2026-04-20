---
title: Query Loop
type: concept
created: 2026-04-08
updated: 2026-04-08
sources:
  - 01_architecture_overview.md
  - claude-code-sourcemap-learning-notebook/en/04_query_loop_api.md
tags:
  - architecture
  - state-machine
  - core-pattern
---

# Query Loop

The central execution model of [[claude-code]]. A `while(true)` + State object state machine in `query.ts` (67KB, 1730 lines) that processes each turn: send messages to the API → stream response → execute tool calls → loop.

## Definition

Unlike recursive designs, the query loop uses an iterative state machine with explicit state transitions. Each iteration: assemble messages → call Claude API → stream response → handle tool calls → check exit conditions → continue or return.

## How It Appears Across Sources

- [[01-architecture-overview]]: introduced as the core data flow, uses [[async-generator-pattern]]
- [[04-query-loop-api]]: full deep dive — state machine design, `transition` field for recovery loop prevention, two tool orchestration modes (streaming vs batch), message queue and interrupt injection

## Key Design Decisions

- **`while(true)` over recursion**: constant memory, no stack depth limits, precise state reset
- **`transition` field**: records WHY the last iteration continued — prevents recovery death loops
- **State pattern**: `(state, event, config) → newState` — immutable config snapshot per query
- **Withhold mode**: recoverable errors detained (not yielded), giving recovery a chance before exposing to user

## Related Concepts

- [[streaming-tool-executor]] — handles concurrent tool execution within each turn
- [[context-management]] — compression triggered when context approaches limits
- [[token-budget]] — governs when to stop continuing
- [[async-generator-pattern]] — streaming mechanism used throughout

## Open Questions

- How does the query loop interact with the upcoming COORDINATOR_MODE feature flag?
- What happens to the state machine when Coordinator mode dispatches to multiple workers simultaneously?

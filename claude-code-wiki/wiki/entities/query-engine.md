---
title: QueryEngine
type: entity
created: 2026-04-08
updated: 2026-04-08
sources: [01_architecture_overview.md, 04_query_loop_api.md, 04b_context_management.md]
tags: [component, core, state-machine]
---

# QueryEngine

The central orchestrator of [[claude-code]], implemented in `QueryEngine.ts` (45KB). Manages session state and drives the [[query-loop]].

## Key Facts

- File: `src/services/QueryEngine.ts` (45KB)
- Owns the REPL message array (conversation history)
- Drives `query()` function with session-level state (API keys, tools, context)
- Creates `QueryConfig` as an immutable snapshot for each query call
- Produces streaming events via AsyncGenerators

## Relationships

- Central component of [[claude-code]]
- Calls `query()` which uses [[streaming-tool-executor]]
- Manages [[context-management]] (compression triggers)
- Interfaces with [[permission-system]] for tool authorization

## Appearances Across Sources

- [[01-architecture-overview]] — introduced as session state manager
- [[04-query-loop-api]] — detailed `query()` function and state machine
- [[04b-context-management]] — compression pipeline integration

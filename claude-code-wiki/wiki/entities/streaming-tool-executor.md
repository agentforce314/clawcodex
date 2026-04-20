---
title: StreamingToolExecutor
type: entity
created: 2026-04-08
updated: 2026-04-08
sources: [04_query_loop_api.md, 05_multi_agent_system.md]
tags: [component, concurrency, core]
---

# StreamingToolExecutor

The concurrency engine within [[claude-code]]'s [[query-loop]]. Handles parallel and sequential tool execution with read-write lock semantics.

## Key Facts

- Implements read-write lock concurrency: `isConcurrencySafe` tools run in parallel (read lock), non-safe tools run exclusively (write lock)
- Uses 3-tier AbortController hierarchy:
  - User Ctrl+C → all tools stop
  - Bash error → sibling tools stop (cascade)
  - Permission denial → bubbles up to parent
- Sibling abort: when any tool in a batch errors, remaining siblings are cancelled
- ~30% latency savings from parallel execution of safe tools
- Processes streaming tool calls as they arrive (doesn't wait for full response)

## Relationships

- Used by [[query-engine]] inside the [[query-loop]]
- Interacts with [[permission-system]] for tool authorization
- Referenced in [[multi-agent-system]] for agent cleanup

## Appearances Across Sources

- [[04-query-loop-api]] — full design and concurrency model
- [[05-multi-agent-system]] — cleanup coordination in agent teardown

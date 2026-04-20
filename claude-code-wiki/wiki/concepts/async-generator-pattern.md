---
title: AsyncGenerator Pattern
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [01_architecture_overview.md, 04_query_loop_api.md]
tags: [design-pattern, streaming, javascript]
---

# AsyncGenerator Pattern

The streaming mechanism used throughout [[claude-code]]: `async function*` generators that yield events as they occur.

## Definition

Instead of returning complete results, [[claude-code]] uses AsyncGenerators to stream partial results (text chunks, tool call starts, tool results) as they arrive from the API. Benefits: memory efficiency (no buffering), natural backpressure, clean abort support via generator `.return()`.

## How It Appears Across Sources

- [[01-architecture-overview]]: introduced as the core streaming mechanism
- [[04-query-loop-api]]: used in the [[query-loop]] for streaming API responses and tool results

## Related Concepts

- [[query-loop]]
- [[streaming-tool-executor]]
- [[context-management]]

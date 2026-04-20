---
title: Tool System
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [02_tool_system.md, 04_query_loop_api.md]
tags: [architecture, tools, core-pattern]
---

# Tool System

The "hands and feet" of [[claude-code]]. A unified framework for defining, registering, filtering, and executing tools — both built-in and external ([[mcp-protocol]]).

## Definition

The Tool interface (`Tool.ts`, 28KB, 793 lines) defines 28+ methods across 7 groups: identity, schema, execution, permissions, prompt, UI, and serialization. All tools are constructed through [[build-tool-pattern]] and assembled into a pool via `assembleToolPool()`.

## How It Appears Across Sources

- [[02-tool-system]]: full interface contract, 9-step [[tool-execution-lifecycle]], prompt engineering techniques, [[feature-flags]] for conditional loading
- [[04-query-loop-api]]: tool execution within the [[query-loop]], concurrency via [[streaming-tool-executor]]

## Key Design Decisions

- **Fail-closed defaults**: `isConcurrencySafe: false`, `isReadOnly: false` — new tools are safe by default
- **Tool list sorted for [[prompt-cache]] stability**: avoids cache invalidation from ordering changes
- **Zod schemas as prompt**: `.describe()` text sent to LLM as part of tool definition
- **`contextModifier`**: only works for non-concurrency-safe tools to prevent race conditions
- **Tool routing in prompts**: dedicated tools prioritized over Bash ("Bash is last resort")

## Related Concepts

- [[build-tool-pattern]]
- [[tool-execution-lifecycle]]
- [[permission-system]]
- [[tool-search]]
- [[mcp-protocol]]

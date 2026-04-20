---
title: "Source: Tool System (Ch.02)"
type: source
created: 2026-04-08
updated: 2026-04-08
sources: [claude-code-sourcemap-learning-notebook/en/02_tool_system.md]
tags: [claude-code, tools, buildTool, zod, prompt-engineering]
---

# Tool System — The Agent's "Hands and Feet"

**Source**: `raw/claude-code-sourcemap-learning-notebook/en/02_tool_system.md`

## Summary

Chapter 2 dissects the [[tool-system]] from interface contract (`Tool.ts`, 28KB, 793 lines) through the [[build-tool-pattern]] factory, a complete GlobTool walkthrough, tool registration/filtering/assembly (`tools.ts`), the execution lifecycle (9 steps: find → parse → validate → permission → hook → execute → hook → serialize → append), and prompt engineering techniques embedded in tool definitions. It also covers [[feature-flags]] for conditional tool loading (18+ flags), `ToolResult` with `contextModifier`, and `maxResultSizeChars` for large result handling.

## Key Claims

- The Tool interface has 28+ methods organized into 7 groups: identity, schema, execution, permissions, prompt, UI, serialization
- `buildTool()` provides fail-closed defaults: `isConcurrencySafe: false`, `isReadOnly: false` — security first
- Zod `.describe()` text is sent to the LLM as part of the prompt — schema doubles as prompt engineering
- Tool list is sorted for [[prompt-cache]] stability; `assembleToolPool()` merges built-in and MCP tools with `uniqBy`
- `contextModifier` only works for non-concurrency-safe tools to avoid race conditions
- `FileReadTool` has `maxResultSizeChars: Infinity` to avoid a Read→file→Read infinite loop
- 5 prompt engineering techniques: tool routing, Zod describe instructions, guidance in results, searchHint, UI collapse control

## Entities Mentioned

- [[claude-code]]
- [[glob-tool]]

## Concepts Mentioned

- [[tool-system]]
- [[build-tool-pattern]]
- [[feature-flags]]
- [[prompt-cache]]
- [[tool-execution-lifecycle]]
- [[prompt-engineering]]

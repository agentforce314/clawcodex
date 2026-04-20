---
title: "Source: Multi-Agent System (Ch.05)"
type: source
created: 2026-04-08
updated: 2026-04-08
sources: [claude-code-sourcemap-learning-notebook/en/05_multi_agent_system.md]
tags: [claude-code, multi-agent, fork-mode, coordinator, isolation]
---

# Multi-Agent System

**Source**: `raw/claude-code-sourcemap-learning-notebook/en/05_multi_agent_system.md`

## Summary

Chapter 5 covers [[claude-code]]'s [[multi-agent-system]]: the evolution from traditional AgentTool → [[fork-mode]] → Coordinator mode. Details the "default isolation, explicit sharing" design principle, [[fork-mode]]'s implementation (full conversation copy + shared prompt cache), the [[coordinator-pattern]] star topology (Coordinator ↔ multiple Workers), 12-step deterministic cleanup, output file transcript mechanism, and how agents communicate through structured tool results rather than shared mutable state.

## Key Claims

- Traditional AgentTool is a single-process coroutine dispatching tasks to sub-agents — each gets a fresh context and forgets everything on exit
- [[fork-mode]] copies the full parent conversation, enabling cache reuse: 80-90% of tokens hit cache (5-10x cheaper)
- Fork decision criterion: "will I need this output again?" — not task size
- "Don't peek" rule: reading fork output mid-flight pulls noise into parent context, defeating the purpose
- "Don't race" rule: never fabricate or predict fork results
- [[coordinator-pattern]]: Coordinator (star center) breaks down tasks, dispatches to Workers, aggregates results
- Workers have `canOnlyUseTools` restrictions — can't launch sub-workers (prevents fork bombs)
- 12-step cleanup: health status → freeze I/O → cancel in-flight → stop streaming → abort timers → collect results → write transcript → clean up subscriptions — all in deterministic order
- Output files use `output_{toolUseId}.md` naming, stored in `getTranscriptDir()`
- Agent communication is unidirectional: parent → child (prompt), child → parent (tool result)

## Entities Mentioned

- [[claude-code]]
- [[streaming-tool-executor]]

## Concepts Mentioned

- [[multi-agent-system]]
- [[fork-mode]]
- [[coordinator-pattern]]
- [[prompt-cache]]
- [[agent-isolation]]

---
title: Multi-Agent System
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [05_multi_agent_system.md]
tags: [architecture, agents, concurrency, core-system]
---

# Multi-Agent System

The agent orchestration architecture in [[claude-code]]: how a main agent spawns, communicates with, and cleans up sub-agents.

## Definition

[[claude-code]] supports multiple agent modes: traditional AgentTool (fresh-context sub-agents), [[fork-mode]] (full conversation copy + cache sharing), and [[coordinator-pattern]] (star topology with Coordinator + Workers). Core principle: **default isolation, explicit sharing**.

## How It Appears Across Sources

- [[05-multi-agent-system]]: full architecture, all three modes, cleanup, communication model

## Key Design Decisions

- **Unidirectional communication**: parent → child (prompt), child → parent (tool result)
- **No shared mutable state**: agents don't share memory, files, or context
- **Workers can't spawn sub-workers**: prevents fork bombs
- **12-step deterministic cleanup**: health → freeze → cancel → stop → abort → collect → write → clean
- **Output transcripts**: `output_{toolUseId}.md` files for debugging and review

## Related Concepts

- [[fork-mode]]
- [[coordinator-pattern]]
- [[agent-isolation]]
- [[prompt-cache]]
- [[streaming-tool-executor]]

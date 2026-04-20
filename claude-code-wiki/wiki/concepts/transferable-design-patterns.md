---
title: Transferable Design Patterns
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [04_query_loop_api.md]
tags: [design-patterns, engineering, applicable]
---

# Transferable Design Patterns

11 engineering patterns extracted from [[claude-code]] that are applicable to any agent system or complex software.

## The Patterns

### From the Query Loop & Tool Execution

1. **Optimistic Recovery** — withhold errors, attempt recovery, expose only on failure
2. **Layered Degradation** — try cheapest solutions first, escalate only when needed
3. **State Machine + Transition Log** — prevent recovery death loops with `transition` field
4. **Read-Write Lock Concurrency** — reads run in parallel, writes run exclusively
5. **Immutable Config Snapshot** — `(state, event, config) → newState` — pure state transitions
6. **Hierarchical Cascade Cancellation** — abort reasons carry semantics (user cancel ≠ sibling error ≠ permission denial)

### From Security

7. **Defense in Depth** — multiple independent layers (see [[defense-in-depth]])
8. **Fail-Closed Defaults** — new tools/features default to most restrictive option

### From Caching & Performance

9. **Cache-Friendly Forking** — child processes share parent's cache prefix
10. **Static/Dynamic Boundary** — cacheable content first, variable content last

### From Prompt Engineering

11. **Layered Prompt Reinforcement** — same rule at system, tool, and result levels

## Related Concepts

- [[query-loop]]
- [[defense-in-depth]]
- [[prompt-cache]]
- [[compression-pipeline]]
- [[fork-mode]]

---
title: Coordinator Pattern
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [05_multi_agent_system.md]
tags: [agents, architecture, feature-flag]
---

# Coordinator Pattern

A star-topology multi-agent orchestration mode in [[claude-code]] where a Coordinator agent breaks down tasks and dispatches to Worker agents.

## Definition

The Coordinator sits at the center, receives the user's task, decomposes it, and dispatches sub-tasks to Workers. Workers execute independently and return results. The Coordinator aggregates and synthesizes. Gated behind the `COORDINATOR_MODE` [[feature-flags|feature flag]].

## Key Design Decisions

- **Star topology**: Coordinator ↔ Workers (no Worker-to-Worker communication)
- **Workers restricted**: `canOnlyUseTools` prevents Workers from spawning sub-workers (fork bomb prevention)
- **Coordinator aggregates**: synthesis happens at the center, not distributed

## Related Concepts

- [[multi-agent-system]]
- [[fork-mode]]
- [[feature-flags]]
- [[agent-isolation]]

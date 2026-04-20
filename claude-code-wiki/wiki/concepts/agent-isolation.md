---
title: Agent Isolation
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [05_multi_agent_system.md]
tags: [agents, security, design-principle]
---

# Agent Isolation

The core design principle of [[claude-code]]'s [[multi-agent-system]]: **default isolation, explicit sharing**.

## Definition

Agents do not share mutable state, memory, or context. Communication is unidirectional: parent → child (via prompt), child → parent (via tool result). Workers cannot spawn sub-workers. Output transcripts are written to files for review but not automatically shared with other agents.

## Key Design Decisions

- No shared mutable state between agents
- Workers restricted via `canOnlyUseTools` — prevents recursive spawning
- Fork copies context but subsequent mutations are independent
- 12-step deterministic cleanup ensures no dangling resources

## Related Concepts

- [[multi-agent-system]]
- [[fork-mode]]
- [[coordinator-pattern]]
- [[defense-in-depth]]

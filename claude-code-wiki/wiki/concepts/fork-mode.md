---
title: Fork Mode
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [05_multi_agent_system.md, 07_prompt_engineering.md]
tags: [agents, concurrency, caching, core-pattern]
---

# Fork Mode

A sub-agent execution mode in [[claude-code]] where the fork receives a full copy of the parent conversation and shares its [[prompt-cache]].

## Definition

When Claude forks itself, the child agent inherits the complete conversation context. Because the conversation prefix is identical, 80-90% of tokens hit the prompt cache, making forks 5-10x cheaper than fresh sub-agents. The fork criterion is qualitative: "will I need this output again?" — not task size.

## How It Appears Across Sources

- [[05-multi-agent-system]]: implementation, cache economics, isolation model
- [[07-prompt-engineering]]: "Don't peek" and "Don't race" rules in AgentTool prompt

## Key Rules

- **Don't peek**: reading fork output mid-flight pulls tool noise into parent context
- **Don't race**: never fabricate or predict fork results
- **Don't set model**: a different model can't reuse parent's cache
- **Research first**: prefer forking research; do research before jumping to implementation

## Related Concepts

- [[multi-agent-system]]
- [[coordinator-pattern]]
- [[prompt-cache]]
- [[agent-isolation]]

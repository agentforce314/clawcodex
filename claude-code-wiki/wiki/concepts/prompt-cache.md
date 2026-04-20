---
title: Prompt Cache
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [02_tool_system.md, 04_query_loop_api.md, 04b_context_management.md, 05_multi_agent_system.md, 07_prompt_engineering.md]
tags: [optimization, cost, caching, core-pattern]
---

# Prompt Cache

A pervasive optimization in [[claude-code]] where static prompt content is cached across API requests to reduce token costs.

## Definition

Anthropic's API supports prompt caching: if the prefix of a request matches a cached prefix, those tokens are served from cache at reduced cost. [[claude-code]] exploits this by carefully structuring prompts with static content first and dynamic content last, separated by `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`.

## How It Appears Across Sources

- [[02-tool-system]]: tool list sorted for cache stability
- [[04-query-loop-api]]: full-chain cache optimization
- [[04b-context-management]]: static `scope: 'global'` sections, ContextCollapseBoundary keeps live context cacheable
- [[05-multi-agent-system]]: [[fork-mode]] shares parent's prompt cache — 80-90% cache hit rate, 5-10x cheaper
- [[07-prompt-engineering]]: 4 specific optimization methods (sort stability, agent list moved out, conditional content postponed, path normalization)

## Key Optimization Methods

1. **Tool list sorting** — stable order ensures prompt prefix unchanged
2. **Agent list in attachment messages** — not in tool descriptions, avoiding invalidation from MCP changes
3. **Conditional content deferred** — runtime conditions placed in dynamic section
4. **Path normalization** — temp dirs replaced with `$TMPDIR` to avoid per-user path differences
5. **Fork cache sharing** — forks reuse parent's cache, making parallel agents cheap

## Related Concepts

- [[context-management]]
- [[fork-mode]]
- [[compression-pipeline]]
- [[prompt-engineering]]

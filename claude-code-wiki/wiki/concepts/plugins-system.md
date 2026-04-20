---
title: Plugins System
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [06_mcp_extensions.md]
tags: [extension, plugins, trust]
---

# Plugins System

A comprehensive extension mechanism in [[claude-code]] providing tools, skills, agents, commands, and hooks in a single package.

## Definition

Plugins bundle multiple extension types: custom tools, skills, agent definitions, slash commands, and hook definitions. Each plugin has a trust level based on its source.

## Trust Hierarchy

| Source | Trust Level |
|--------|-------------|
| `builtin` | Fully trusted |
| `plugin` | Admin trusted |
| `policySettings` | Admin trusted (enterprise) |
| `user` | Restricted trust |

In `strictPluginOnlyCustomization` mode, only builtin/plugin/policySettings extensions load — user-defined MCP, hooks, and agents are blocked.

## Related Concepts

- [[mcp-protocol]]
- [[skills-system]]
- [[tool-system]]
- [[defense-in-depth]]

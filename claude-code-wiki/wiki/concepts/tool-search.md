---
title: ToolSearch (Lazy Loading)
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [06_mcp_extensions.md]
tags: [optimization, tools, token-management]
---

# ToolSearch — Lazy Loading Tools

A token optimization mechanism in [[claude-code]] that avoids sending all tool schemas in every API request.

## Definition

When many tools exist (built-in + MCP), sending all full schemas consumes massive tokens. ToolSearch sends only core tools with full schema initially; other tools are represented by name only (`defer_loading`). When Claude needs a lazy-loaded tool, it calls the ToolSearch tool, which returns matching tools, and their full schemas are included in the next request.

## Key Design Decisions

- Core tools (Bash, Read, ToolSearch itself) always include full schema
- MCP tools can opt out via `_meta['anthropic/alwaysLoad'] = true`
- Search uses `searchHint` field on tools for better discoverability
- Only frontmatter tokens estimated for skills (not full content)

## Related Concepts

- [[tool-system]]
- [[mcp-protocol]]
- [[prompt-cache]]
- [[token-budget]]

---
title: "Source: MCP Protocol, Skills & Extensions (Ch.06)"
type: source
created: 2026-04-08
updated: 2026-04-08
sources: [claude-code-sourcemap-learning-notebook/en/06_mcp_extensions.md]
tags: [claude-code, mcp, skills, plugins, tool-search, extension-system]
---

# MCP Protocol, Skills & Extension System

**Source**: `raw/claude-code-sourcemap-learning-notebook/en/06_mcp_extensions.md`

## Summary

Chapter 6 covers the [[mcp-protocol]] client implementation, tool wrapping (`mcp__serverName__toolName` naming), [[tool-search]] lazy loading, the three-layer [[skills-system]] (bundled → disk-based → MCP), bundled skill registration with lazy file extraction and security design, key built-in skills (/simplify, /skillify, /remember, /stuck), skill loading deduplication via realpath, and the [[plugins-system]] trust hierarchy.

## Key Claims

- [[mcp-protocol]] supports 5 transport methods: Stdio (most common), SSE, StreamableHTTP, WebSocket, SdkControl
- MCP tools wrapped via `createMcpTool()` with `mcp__serverName__toolName` naming convention
- [[tool-search]] sends only core tools with full schema initially; lazy-loaded tools send name only, loaded on demand via ToolSearch tool call
- Skills three-layer: bundled (compiled into binary), disk-based (Markdown + frontmatter), MCP (dynamic from servers)
- Disk-based skill frontmatter supports `context: fork` (isolated sub-agent) and `allowed-tools` (parameterized permissions like `Bash(gh:*)`)
- Bundled skill file extraction uses 6-layer defense: nonce directory, 0o700 mkdir, O_NOFOLLOW, O_EXCL, path validation, no unlink+retry
- /simplify launches 3 parallel agents: code reuse, quality, efficiency
- /skillify uses 4-round interactive interview to convert sessions into reusable skills
- /remember manages memory hierarchy: CLAUDE.md → CLAUDE.local.md → auto-memory → team memory
- Skill token estimation only counts frontmatter (name + description + whenToUse), not full content
- Plugin trust hierarchy: builtin > plugin/policySettings > user; strictPluginOnlyCustomization blocks user-defined extensions

## Entities Mentioned

- [[claude-code]]

## Concepts Mentioned

- [[mcp-protocol]]
- [[tool-search]]
- [[skills-system]]
- [[plugins-system]]
- [[defense-in-depth]]
- [[prompt-cache]]

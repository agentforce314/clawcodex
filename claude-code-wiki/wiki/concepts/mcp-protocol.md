---
title: MCP Protocol
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [06_mcp_extensions.md]
tags: [protocol, integration, external-tools]
---

# MCP Protocol (Model Context Protocol)

An open protocol proposed by [[anthropic]] to standardize AI model interactions with external tools and data sources.

## Definition

MCP defines a JSON-RPC 2.0 based protocol between an MCP Client (e.g., [[claude-code]]) and MCP Servers (written in any language). Servers can provide three capabilities: Tools (executable operations), Resources (readable data), and Prompts (predefined templates).

## How It Appears Across Sources

- [[06-mcp-extensions]]: client implementation (116KB), transport methods, tool wrapping, configuration hierarchy

## Key Design Decisions

- **5 transports**: Stdio (most common), SSE, StreamableHTTP, WebSocket, SdkControl
- **Tool naming**: `mcp__serverName__toolName` convention
- **Configuration hierarchy**: project-level overrides user-level; enterprise policy has highest priority
- **Error classes**: `McpAuthError` (re-auth), `McpSessionExpiredError` (reconnect), `McpToolCallError` (return error)
- **`alwaysLoad` flag**: tools marked with `_meta['anthropic/alwaysLoad']` skip [[tool-search]] lazy loading

## Related Concepts

- [[tool-system]]
- [[tool-search]]
- [[skills-system]]
- [[plugins-system]]

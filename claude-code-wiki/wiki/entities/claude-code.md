---
title: Claude Code
type: entity
created: 2026-04-08
updated: 2026-04-08
sources: [00_index.md, 01_architecture_overview.md, 02_tool_system.md, 03_permission_security.md, 04_query_loop_api.md, 04b_context_management.md, 05_multi_agent_system.md, 06_mcp_extensions.md, 07_prompt_engineering.md, 08_voice_buddy.md]
tags: [product, agent, cli, anthropic]
---

# Claude Code

[[anthropic]]'s official CLI agent for software engineering tasks. Available as CLI, desktop app (Mac/Windows), web app (claude.ai/code), and IDE extensions (VS Code, JetBrains).

## Key Facts

- **Codebase**: 512K+ lines of TypeScript
- **Runtime**: Bun
- **UI Framework**: React/Ink (terminal rendering)
- **CLI Framework**: Commander.js
- **Largest file**: `main.tsx` (785KB) — CLI argument definitions and initialization
- **Core loop**: `query.ts` (67KB, 1730 lines) — [[query-loop]] state machine
- **Security**: `bashSecurity.ts` (~100KB) — command safety analysis
- **MCP Client**: `services/mcp/client.ts` (116KB, 3349 lines)
- **Prompts**: 150KB+ across 40+ files

## Architecture

Three entry modes feed into a unified core:
1. **CLI/REPL** — direct terminal usage
2. **Bridge/IDE** — VS Code, JetBrains extensions
3. **SDK/Headless** — programmatic integration

Core flow: App State → [[query-engine]] → [[tool-system]] + Claude API + [[permission-system]]

## Appearances Across Sources

- [[00-index-architecture-overview]] — overall structure and learning roadmap
- [[01-architecture-overview]] — tech stack, directory structure, data flow
- [[02-tool-system]] — 40+ tools, [[build-tool-pattern]], execution lifecycle
- [[03-permission-security]] — 5-layer [[defense-in-depth]]
- [[04-query-loop-api]] — [[query-loop]], [[streaming-tool-executor]], error recovery
- [[04b-context-management]] — [[compression-pipeline]], token management
- [[05-multi-agent-system]] — [[fork-mode]], [[coordinator-pattern]]
- [[06-mcp-extensions]] — [[mcp-protocol]], [[skills-system]], [[plugins-system]]
- [[07-prompt-engineering]] — 7-module system prompt, 8 prompt tips
- [[08-voice-buddy]] — [[voice-mode]], [[buddy-system]]

## Relationships

- Built by [[anthropic]]
- Core component: [[query-engine]]
- Core component: [[streaming-tool-executor]]
- Uses: [[mcp-protocol]] for external tool integration

---
title: Wiki Log
type: overview
created: 2026-04-08
updated: 2026-04-08
tags: [meta, log]
---

# Wiki Log

## [2026-04-08] maintain | Wiki Initialized

Wiki structure created. Directories: `raw/`, `raw/assets/`, `wiki/sources/`, `wiki/entities/`, `wiki/concepts/`, `wiki/analyses/`. Schema defined in `CLAUDE.md`. Index and log files created. Ready for first ingest.

## [2026-04-08] ingest | Claude Code Sourcemap Learning Notebook (10 chapters)

Batch ingest of `raw/claude-code-sourcemap-learning-notebook/` — a reverse-engineering study of Claude Code's 512K+ line TypeScript codebase. 10 source files covering architecture, tools, security, query loop, context management, multi-agent system, MCP/extensions, prompt engineering, and voice/buddy features.

**Pages created (35 total):**

Sources (10):
- `wiki/sources/00-index-architecture-overview.md`
- `wiki/sources/01-architecture-overview.md`
- `wiki/sources/02-tool-system.md`
- `wiki/sources/03-permission-security.md`
- `wiki/sources/04-query-loop-api.md`
- `wiki/sources/04b-context-management.md`
- `wiki/sources/05-multi-agent-system.md`
- `wiki/sources/06-mcp-extensions.md`
- `wiki/sources/07-prompt-engineering.md`
- `wiki/sources/08-voice-buddy.md`

Entities (5):
- `wiki/entities/claude-code.md`
- `wiki/entities/anthropic.md`
- `wiki/entities/query-engine.md`
- `wiki/entities/streaming-tool-executor.md`
- `wiki/entities/glob-tool.md`

Concepts (20):
- `wiki/concepts/query-loop.md`
- `wiki/concepts/tool-system.md`
- `wiki/concepts/build-tool-pattern.md`
- `wiki/concepts/tool-execution-lifecycle.md`
- `wiki/concepts/tool-search.md`
- `wiki/concepts/defense-in-depth.md`
- `wiki/concepts/permission-system.md`
- `wiki/concepts/permission-modes.md`
- `wiki/concepts/sandbox-system.md`
- `wiki/concepts/hook-system.md`
- `wiki/concepts/context-management.md`
- `wiki/concepts/compression-pipeline.md`
- `wiki/concepts/token-budget.md`
- `wiki/concepts/autocompact.md`
- `wiki/concepts/prompt-cache.md`
- `wiki/concepts/prompt-engineering.md`
- `wiki/concepts/session-memory.md`
- `wiki/concepts/multi-agent-system.md`
- `wiki/concepts/fork-mode.md`
- `wiki/concepts/coordinator-pattern.md`
- `wiki/concepts/agent-isolation.md`
- `wiki/concepts/mcp-protocol.md`
- `wiki/concepts/skills-system.md`
- `wiki/concepts/plugins-system.md`
- `wiki/concepts/feature-flags.md`
- `wiki/concepts/transferable-design-patterns.md`
- `wiki/concepts/async-generator-pattern.md`
- `wiki/concepts/message-type-system.md`
- `wiki/concepts/voice-mode.md`
- `wiki/concepts/buddy-system.md`

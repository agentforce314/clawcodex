---
title: Wiki Index
type: overview
created: 2026-04-08
updated: 2026-04-08
tags: [meta, index]
---

# Wiki Index

## Sources

- [[00-index-architecture-overview]] — Learning notebook index, architecture diagram, learning paths (2026-04-08)
- [[01-architecture-overview]] — Tech stack, directory structure, data flow, feature flags (2026-04-08)
- [[02-tool-system]] — Tool interface, buildTool pattern, execution lifecycle, prompt techniques (2026-04-08)
- [[03-permission-security]] — 5-layer defense in depth, permission rules, sandbox (2026-04-08)
- [[04-query-loop-api]] — Query loop state machine, StreamingToolExecutor, error recovery, 6 design patterns (2026-04-08)
- [[04b-context-management]] — 5-layer compression pipeline, token counting, prompt cache (2026-04-08)
- [[05-multi-agent-system]] — Fork mode, Coordinator pattern, agent isolation, cleanup (2026-04-08)
- [[06-mcp-extensions]] — MCP protocol, ToolSearch, skills system, plugins (2026-04-08)
- [[07-prompt-engineering]] — 150KB+ prompts, 7 modules, 8 tips, coding philosophy (2026-04-08)
- [[08-voice-buddy]] — Voice mode STT, buddy companion, creative features (2026-04-08)

## Entities

- [[claude-code]] — Anthropic's official CLI agent for software engineering (512K+ lines TypeScript)
- [[anthropic]] — AI safety company, creator of Claude and Claude Code
- [[query-engine]] — Central orchestrator, manages session state, drives query loop (QueryEngine.ts, 45KB)
- [[streaming-tool-executor]] — Concurrency engine with read-write lock semantics and cascade abort
- [[glob-tool]] — Built-in file pattern matching tool, canonical buildTool example

## Concepts

- [[query-loop]] — while(true) + State object state machine, core execution model
- [[tool-system]] — 40+ tools, unified framework for defining and executing agent capabilities
- [[build-tool-pattern]] — Factory function with fail-closed defaults
- [[tool-execution-lifecycle]] — 9-step process from find to append
- [[tool-search]] — Lazy loading mechanism to reduce token consumption
- [[defense-in-depth]] — Multiple independent security layers
- [[permission-system]] — Multi-source rule system (DENY > ASK > ALLOW)
- [[permission-modes]] — 5 user-facing presets (default, plan, bypass, acceptEdits, auto)
- [[sandbox-system]] — macOS Seatbelt process sandbox, outermost security layer
- [[hook-system]] — User-configurable event-driven shell commands
- [[context-management]] — Fighting the context limit with 5-layer compression
- [[compression-pipeline]] — toolResultBudget → snip → microcompact → contextCollapse → autocompact
- [[token-budget]] — Output token allocation and diminishing returns detection
- [[autocompact]] — LLM-based compression with 9-section structured template
- [[prompt-cache]] — Static/dynamic boundary for API cost optimization
- [[prompt-engineering]] — 150KB+ of prompts, 8 transferable tips, coding philosophy
- [[session-memory]] — Cross-session persistence with fixed-structure Markdown template
- [[multi-agent-system]] — Agent orchestration: traditional, fork, coordinator modes
- [[fork-mode]] — Full conversation copy + shared prompt cache (5-10x cheaper)
- [[coordinator-pattern]] — Star-topology task decomposition (Coordinator + Workers)
- [[agent-isolation]] — Default isolation, explicit sharing, no shared mutable state
- [[mcp-protocol]] — Open protocol for AI-tool interaction (JSON-RPC 2.0)
- [[skills-system]] — Three-layer reusable workflows (bundled, disk-based, MCP)
- [[plugins-system]] — Comprehensive extension packages with trust hierarchy
- [[feature-flags]] — Compile-time and runtime feature gating
- [[transferable-design-patterns]] — 11 engineering patterns applicable to any agent system
- [[async-generator-pattern]] — Streaming mechanism using async function* generators
- [[message-type-system]] — Typed message representation for all conversation events
- [[voice-mode]] — Hold-to-talk voice input via WebSocket STT
- [[buddy-system]] — Deterministic ASCII companion generated from userId hash

## Analyses

_No analysis pages yet._

---
title: "Source: Index & Architecture Overview (Ch.00)"
type: source
created: 2026-04-08
updated: 2026-04-08
sources: [claude-code-sourcemap-learning-notebook/en/00_index.md]
tags: [claude-code, architecture, index]
---

# Index & Architecture Overview

**Source**: `raw/claude-code-sourcemap-learning-notebook/en/00_index.md`

## Summary

This is the index file for the Claude Code Sourcemap Learning Notebook, a reverse-engineering study of [[claude-code]]'s 512K+ lines of TypeScript. It provides the learning roadmap, architecture diagram, suggested learning paths, and key file reference table for the entire 9-chapter course (~6 hours total).

## Key Claims

- [[claude-code]] is built on TypeScript + React/Ink (terminal UI) + Bun (runtime)
- The architecture has three entry modes: CLI/REPL, Bridge/IDE (VS Code), and SDK/Headless
- Central flow: App State → [[query-engine]] → [[tool-system]] + Claude API + [[permission-system]]
- Core files: `main.tsx` (785KB), `query.ts` (67KB), `QueryEngine.ts` (45KB), `bashSecurity.ts` (100KB), `mcp/client.ts` (116KB)

## Entities Mentioned

- [[claude-code]]
- [[anthropic]]

## Concepts Mentioned

- [[query-loop]]
- [[tool-system]]
- [[permission-system]]
- [[multi-agent-system]]
- [[mcp-protocol]]
- [[prompt-engineering]]
- [[context-management]]

## Learning Paths

| Path | Chapters | Time |
|------|----------|------|
| Beginner | 01 → 02 → 04 | 2h |
| Security Research | 03 → 02 | 1.5h |
| Agent Research | 05 → 04 → 06 | 2.5h |
| Prompt Research | 07 | 1h |
| Full Course | 01 → 08 | 6h |

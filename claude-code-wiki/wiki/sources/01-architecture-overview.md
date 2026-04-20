---
title: "Source: Global Architecture Overview (Ch.01)"
type: source
created: 2026-04-08
updated: 2026-04-08
sources: [claude-code-sourcemap-learning-notebook/en/01_architecture_overview.md]
tags: [claude-code, architecture, react-ink, bun, query-loop]
---

# Global Architecture Overview

**Source**: `raw/claude-code-sourcemap-learning-notebook/en/01_architecture_overview.md`

## Summary

Chapter 1 establishes what [[claude-code]] is and traces a complete message journey from user input to AI response. It covers the tech stack (TypeScript, Bun, React/Ink, Commander.js), the directory structure of the `src/` tree, the core data flow through [[query-engine]] and `query()`, the [[feature-flags]] system using `bun:bundle`'s `feature()` for compile-time dead code elimination, the message type system, and key design patterns (AsyncGenerator, [[build-tool-pattern]], dead code elimination).

## Key Claims

- `main.tsx` is 785KB — the largest file, containing all CLI argument definitions and initialization
- [[query-engine]] (`QueryEngine.ts`, 45KB) manages session state and drives the [[query-loop]]
- The entire query loop uses `async function*` (AsyncGenerators) for streaming, memory efficiency, and abort support
- [[feature-flags]]: 10+ experimental flags (`COORDINATOR_MODE`, `KAIROS`, `PROACTIVE`, `WEB_BROWSER_TOOL`, etc.) mark Anthropic's cutting-edge explorations
- Context (`context.ts`) is memoized — computed once per session, producing a snapshot-in-time

## Entities Mentioned

- [[claude-code]]
- [[anthropic]]
- [[query-engine]]

## Concepts Mentioned

- [[query-loop]]
- [[build-tool-pattern]]
- [[feature-flags]]
- [[async-generator-pattern]]
- [[context-management]]
- [[message-type-system]]

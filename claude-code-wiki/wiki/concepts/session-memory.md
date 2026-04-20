---
title: Session Memory
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [07_prompt_engineering.md]
tags: [memory, persistence, cross-session]
---

# Session Memory

Cross-session memory system in [[claude-code]] that persists context across conversations.

## Definition

Session memory uses a fixed-structure Markdown template with immutable section headers. Claude updates only the content below headers. Budget: 2000 tokens per section, 12000 total. The /remember skill manages a memory hierarchy: CLAUDE.md (project, shared) → CLAUDE.local.md (personal, not in git) → auto-memory (auto-extracted) → team memory (org-level, cross-repo).

## Template Sections

1. Session Title
2. Current State
3. Task Specification
4. Files and Functions
5. Workflow
6. Errors & Corrections
7. Codebase and System Documentation
8. Learnings
9. Key Results
10. Worklog

## Key Design Decisions

- Structure immutable — only content changes, never headers
- Token budget per section prevents bloat
- /remember classifies and promotes notes across hierarchy levels
- Update prompt explicitly says "these instructions are NOT part of the user conversation"

## Related Concepts

- [[context-management]]
- [[autocompact]]
- [[prompt-engineering]]

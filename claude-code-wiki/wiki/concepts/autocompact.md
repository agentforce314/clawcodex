---
title: Autocompact
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [04b_context_management.md, 07_prompt_engineering.md]
tags: [compression, context-window, llm-call]
---

# Autocompact

The most expensive layer of the [[compression-pipeline]]: a full LLM-based compression of conversation history using a 9-section structured template.

## Definition

When cheaper compression layers are exhausted, autocompact sends the conversation to Claude with a specialized prompt requiring a structured summary (9 sections). Uses `maxTurns: 1` — only one chance. The result replaces old context with a compressed summary disguised as a "user message."

## 9 Required Sections

1. Primary Request and Intent
2. Key Technical Concepts
3. Files and Code Sections (with snippets)
4. Errors and Fixes
5. Problem Solving
6. All User Messages (prevents losing feedback)
7. Pending Tasks
8. Current Work (with file names and snippets)
9. Optional Next Step (with direct quotes)

## Key Design Decisions

- **No tools allowed**: emphatic "do NOT call any tools" at beginning AND end (2.79% failure rate on Sonnet 4.6+)
- **Two-stage output**: `<analysis>` (draft, deleted) + `<summary>` (final result)
- **Direct quotes required**: Section 9 requires quotes to prevent information drift
- **Automatic continuation**: post-compact message tells Claude to "pretend the interruption never happened"
- **Transcript backup**: full conversation file path provided for detail retrieval

## Related Concepts

- [[compression-pipeline]]
- [[context-management]]
- [[session-memory]]
- [[prompt-engineering]]

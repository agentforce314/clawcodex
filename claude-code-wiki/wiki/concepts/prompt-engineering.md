---
title: Prompt Engineering
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [02_tool_system.md, 07_prompt_engineering.md]
tags: [prompts, design, core-system]
---

# Prompt Engineering

The craft of designing prompts that guide [[claude-code]]'s behavior. 150KB+ of prompt text across 40+ files, organized into 7 categories.

## Definition

[[claude-code]]'s prompt system comprises: system prompts (identity, security, behavior, style), tool prompts (per-tool operation manuals), agent prompts (sub-agent briefing rules), compact prompts (compression templates), security prompts (classifiers), session memory prompts, and extension prompts.

## 8 Transferable Tips (from Ch.07)

1. **Layered defense**: same rule reinforced at system, tool, and result levels
2. **Bookend reinforcement**: critical constraints at both beginning and end
3. **Specific examples > abstract rules**: list exact dangerous commands, don't just say "be careful"
4. **Explain WHY**: "pre-commit hook fail means commit didn't happen, so --amend would modify PREVIOUS commit"
5. **Zod describe as prompt**: behavioral instructions embedded in parameter schema
6. **Guidance in results**: tool outputs include suggestions for next steps
7. **Meta-prompt pattern**: prompts that generate prompts (Agent Creator)
8. **Cache-aware design**: static first, dynamic last, boundary marker separation

## Core Coding Philosophy (from system prompt)

| Principle | Meaning |
|-----------|---------|
| No gold plating | Do only what's asked |
| No premature abstraction | Three similar lines > premature helper |
| Validate at boundaries only | Trust internal code, validate user input and external APIs |
| Read before modify | Understand first |
| Diagnose before retry | Analyze failure cause, don't retry blindly |

## Related Concepts

- [[prompt-cache]]
- [[defense-in-depth]]
- [[context-management]]
- [[autocompact]]
- [[session-memory]]

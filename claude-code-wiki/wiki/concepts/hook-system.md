---
title: Hook System
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [03_permission_security.md]
tags: [extensibility, security, events]
---

# Hook System

User-configurable shell commands in [[claude-code]] that execute in response to events (tool calls, prompts, etc.).

## Definition

Hooks allow users to run custom shell commands at specific lifecycle events (PreToolUse, PostToolUse, user-prompt-submit). Hook feedback is treated as user input. Critical security constraint: hooks can only raise security level, never lower it — a hook returning `allow` does NOT bypass deny/ask rules.

## Key Design Decisions

- PreToolUse hooks can block tool execution
- Hook output treated as coming from the user
- Hooks cannot override deny rules — security monotonicity
- Configurable via settings files

## Related Concepts

- [[permission-system]]
- [[defense-in-depth]]
- [[tool-execution-lifecycle]]

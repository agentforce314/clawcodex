---
title: Permission Modes
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [03_permission_security.md]
tags: [security, permissions, user-facing]
---

# Permission Modes

The user-facing permission presets in [[claude-code]] that determine how tool calls are authorized.

## Definition

Five modes govern the default behavior when no explicit rule matches:

| Mode | Behavior |
|------|----------|
| `default` | Ask for everything not explicitly allowed |
| `plan` | Read-only — blocks all write operations |
| `bypassPermissions` | Allow everything (but safety floor still applies) |
| `acceptEdits` | Auto-approve file edits, ask for Bash |
| `auto` | Use [[yolo-classifier]] (51KB prompt) to classify commands automatically |

## Key Design Decisions

- Even `bypassPermissions` has a safety floor: `.git/`, `.claude/`, `.vscode/`, shell configs, `.ssh/` always ask
- `auto` mode uses a dedicated LLM classifier prompt (51KB) — the largest single prompt in the system
- Mode is checked as Layer 2 of [[defense-in-depth]], after permission rules

## Related Concepts

- [[permission-system]]
- [[defense-in-depth]]
- [[sandbox-system]]

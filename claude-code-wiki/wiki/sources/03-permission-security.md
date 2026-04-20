---
title: "Source: Permission & Security System (Ch.03)"
type: source
created: 2026-04-08
updated: 2026-04-08
sources: [claude-code-sourcemap-learning-notebook/en/03_permission_security.md]
tags: [claude-code, security, permissions, defense-in-depth, sandbox]
---

# Permission & Security System

**Source**: `raw/claude-code-sourcemap-learning-notebook/en/03_permission_security.md`

## Summary

Chapter 3 covers the [[defense-in-depth]] security architecture of [[claude-code]]: 5 independent layers (permission rules → permission mode → tool-specific checks → path safety → macOS Seatbelt sandbox). It details the complete permission check flow through `hasPermissionsToUseToolInner()`, the rule system (deny/ask/allow with DENY > ASK > ALLOW priority), permission modes (default/plan/bypassPermissions/acceptEdits/auto), BashTool's command security analysis (`bashSecurity.ts`, ~100KB), filesystem permission checks, the hook system's interaction with permissions, and dangerous permission detection.

## Key Claims

- 5-layer [[defense-in-depth]]: rules → mode → tool checks → path safety → sandbox
- Rule priority: DENY > ASK > ALLOW — fundamental security principle
- Even `bypassPermissions` mode has a safety floor: `.git/`, `.claude/`, `.vscode/`, shell configs, `.ssh/` always require confirmation
- `bashSecurity.ts` (~100KB) is the largest security file, analyzing shell command safety with pattern matching
- Permission rules come from 6 sources with priority: cliArg, session, localSettings, projectSettings, policySettings, command
- Hook system: PreToolUse hooks returning `allow` do NOT bypass deny/ask rules — hooks can only raise security, never lower it
- Dangerous permission detection blocks overly broad rules like `Bash(*)` or `Bash(python:*)`

## Entities Mentioned

- [[claude-code]]

## Concepts Mentioned

- [[defense-in-depth]]
- [[permission-system]]
- [[permission-modes]]
- [[sandbox-system]]
- [[hook-system]]

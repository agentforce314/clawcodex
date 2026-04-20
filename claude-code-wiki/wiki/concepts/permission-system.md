---
title: Permission System
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [03_permission_security.md]
tags: [security, permissions, core-system]
---

# Permission System

The authorization framework in [[claude-code]] that gates tool execution. Every tool call passes through `hasPermissionsToUseToolInner()` before execution.

## Definition

A multi-source rule system where rules come from 6 sources (prioritized): cliArg, session, localSettings, projectSettings, policySettings, command. Each rule can be deny, ask, or allow. Priority: DENY > ASK > ALLOW.

## How It Appears Across Sources

- [[03-permission-security]]: complete flow, rule sources, dangerous permission detection

## Key Design Decisions

- Rules are parameterized: `Bash(git:*)` allows only git commands, `Bash(gh:*)` allows only GitHub CLI
- Dangerous permission detection blocks overly broad rules like `Bash(*)` or `Bash(python:*)`
- 6 rule sources with strict priority ordering
- Part of the larger [[defense-in-depth]] architecture

## Related Concepts

- [[defense-in-depth]]
- [[permission-modes]]
- [[sandbox-system]]
- [[hook-system]]
- [[tool-execution-lifecycle]]

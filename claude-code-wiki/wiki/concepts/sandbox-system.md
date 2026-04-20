---
title: Sandbox System
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [03_permission_security.md, 07_prompt_engineering.md]
tags: [security, sandbox, macos, isolation]
---

# Sandbox System

The outermost security layer in [[claude-code]]: an OS-level process sandbox that constrains file and network access regardless of permission rules.

## Definition

On macOS, [[claude-code]] uses Apple's Seatbelt (`sandbox-exec`) to create a sandbox profile restricting filesystem reads/writes and network access. The sandbox configuration is serialized into the BashTool prompt so Claude understands the constraints and can diagnose sandbox-caused failures.

## How It Appears Across Sources

- [[03-permission-security]]: Layer 5 of [[defense-in-depth]], filesystem and network restrictions
- [[07-prompt-engineering]]: BashTool prompt includes serialized sandbox config; specifies when sandbox bypass is permitted

## Key Design Decisions

- Sandbox config has allow/deny lists for both filesystem and network
- Claude should default to running in sandbox; bypass only when user explicitly asks or evidence of sandbox-caused failure
- Evidence of sandbox failures: "Operation not permitted", access denied, connection failures to non-whitelisted hosts

## Related Concepts

- [[defense-in-depth]]
- [[permission-system]]
- [[permission-modes]]

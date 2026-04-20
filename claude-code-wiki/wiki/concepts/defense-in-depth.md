---
title: Defense in Depth
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [03_permission_security.md, 06_mcp_extensions.md, 07_prompt_engineering.md]
tags: [security, design-pattern, core-principle]
---

# Defense in Depth

A core security philosophy in [[claude-code]]: multiple independent layers of protection so that no single failure compromises the system.

## Definition

Defense in depth means stacking independent security mechanisms so each layer catches what the previous might miss. In Claude Code, this manifests as 5 layers for permissions, 6 layers for skill file extraction, and multi-level prompt reinforcement.

## How It Appears Across Sources

- [[03-permission-security]]: **5-layer permission model** — permission rules → permission mode → tool-specific checks → path safety → macOS Seatbelt sandbox
- [[06-mcp-extensions]]: **6-layer file extraction security** — nonce directory → 0o700 mkdir → O_NOFOLLOW → O_EXCL → path validation → no unlink+retry
- [[07-prompt-engineering]]: **layered prompt defense** — same rule reinforced at system prompt, tool prompt, and tool result levels

## Key Principles

- **DENY > ASK > ALLOW**: deny rules always win regardless of source
- **Safety floor**: even `bypassPermissions` mode blocks access to `.git/`, `.claude/`, `.ssh/`, shell configs
- **Hooks can only raise security**: PreToolUse hooks returning `allow` don't bypass deny/ask rules
- **Independent layers**: each layer works even if others fail

## Related Concepts

- [[permission-system]]
- [[permission-modes]]
- [[sandbox-system]]
- [[build-tool-pattern]] (fail-closed defaults)

## Tensions and Debates

The tension between security and usability is visible in the permission modes: `default` mode asks for everything, `auto` mode tries to classify commands automatically (via `bashSecurity.ts`, ~100KB), and `bypassPermissions` removes most checks but keeps a safety floor. The auto mode classifier has a 51KB prompt — reflecting the difficulty of automated security decisions.

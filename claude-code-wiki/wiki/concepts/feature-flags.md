---
title: Feature Flags
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [01_architecture_overview.md, 02_tool_system.md, 08_voice_buddy.md]
tags: [architecture, build-system, experimentation]
---

# Feature Flags

Compile-time and runtime flags in [[claude-code]] that enable/disable features for different builds and users.

## Definition

[[claude-code]] uses `bun:bundle`'s `feature()` function for compile-time dead code elimination. Features not included in a build are completely removed from the output — no runtime overhead. Additionally, GrowthBook provides runtime feature gating for gradual rollouts and kill-switches.

## Known Feature Flags

| Flag | Purpose |
|------|---------|
| `COORDINATOR_MODE` | Star-topology [[coordinator-pattern]] |
| `KAIROS` / `KAIROS_DREAM` | Dream skill (experimental) |
| `PROACTIVE` | Proactive agent behavior |
| `WEB_BROWSER_TOOL` | Web browsing capability |
| `AGENT_TRIGGERS` | Loop skill |
| `VOICE_MODE` | [[voice-mode]] hold-to-talk |

## Key Design Decisions

- Compile-time elimination: feature-gated code uses `require()` not `import` to keep it out of the bundle
- GrowthBook kill-switches default to "not disabled" — new installs work without waiting for init
- 18+ flags for conditional tool loading identified in tool system

## Related Concepts

- [[tool-system]]
- [[defense-in-depth]]
- [[voice-mode]]
- [[buddy-system]]

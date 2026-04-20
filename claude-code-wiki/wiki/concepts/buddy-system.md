---
title: Buddy System
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [08_voice_buddy.md]
tags: [feature, creative, companion, ascii-art]
---

# Buddy System

Virtual ASCII companion system in [[claude-code]], activated via `/buddy` command.

## Definition

Each user gets a deterministic companion generated from `hash(userId)` via Mulberry32 PRNG. Companions have bones (species, rarity, eye, hat, shiny, stats — regenerated each time) and a soul (name, personality — persisted). 18 species, 5 rarities (common 60% → legendary 1%), 5 stats (DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK) with one peak and one dump.

## Key Design Decisions

- **Bones not persisted**: species renames can't break saves; config editing can't fake rarity
- **charCode encoding**: species names constructed at runtime to bypass build system codename checks
- **Role separation prompt**: Claude told "You're not {name}" / "stay out of the way" — prevents Claude from breaking companion immersion
- **Local time teaser**: April 1-7 2026 teaser window uses local time for 24h rolling discovery across timezones
- **Single-entry cache**: `roll()` result cached per userId; sufficient since userId doesn't change per process

## Related Concepts

- [[voice-mode]]
- [[feature-flags]]
- [[prompt-engineering]]

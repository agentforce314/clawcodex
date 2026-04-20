---
title: "Source: Voice Mode & Buddy System (Ch.08)"
type: source
created: 2026-04-08
updated: 2026-04-08
sources: [claude-code-sourcemap-learning-notebook/en/08_voice_buddy.md]
tags: [claude-code, voice-mode, buddy-system, creative-features]
---

# Voice Mode & Buddy System

**Source**: `raw/claude-code-sourcemap-learning-notebook/en/08_voice_buddy.md`

## Summary

Chapter 8 covers two distinctive features: [[voice-mode]] (hold-to-talk voice input) and the [[buddy-system]] (virtual ASCII companion). Voice mode traces the full pipeline from key hold → recording → WebSocket STT (Deepgram Nova 3) → text injection, with three-layer availability checks (feature gate + OAuth + GrowthBook kill-switch). Buddy system covers deterministic generation via `hash(userId) → PRNG → bones`, the bones/soul separation (bones regenerated, soul persisted), ASCII sprite animation, stats generation (one peak, one dump stat), prompt injection for Claude-Buddy coexistence, and the April 2026 teaser window using local time for rolling discovery.

## Key Claims

- Voice: records immediately before WebSocket connects, buffers audio, flushes on ready — eliminates 1-2s delay
- Voice: uses `api.anthropic.com` instead of `claude.ai` to bypass Cloudflare TLS fingerprint (JA3) detection
- Voice: `no_data_timeout` detects ~1% "sticky bad pod" silent drops; retries with full audio replay
- Voice: auto-repeat key detection used because terminals can't directly detect key release
- Buddy: Mulberry32 PRNG — "good enough for picking ducks" — deterministic from userId hash
- Buddy: bones never persisted → species renames can't break saves, config editing can't fake rarity
- Buddy: species names encoded with `String.fromCharCode` to bypass build system model codename checks
- Buddy: prompt design uses role separation — "You're not {name}" / "stay out of the way" / "Don't narrate what {name} might say"
- Buddy: teaser uses local time (not UTC) for 24h rolling wave across timezones — sustained social media buzz, smoother server load
- Buddy: 5 stats (DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK), rarities from common(60%) to legendary(1%)

## Entities Mentioned

- [[claude-code]]
- [[anthropic]]

## Concepts Mentioned

- [[voice-mode]]
- [[buddy-system]]
- [[feature-flags]]
- [[prompt-engineering]]

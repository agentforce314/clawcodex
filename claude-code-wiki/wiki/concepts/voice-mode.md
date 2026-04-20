---
title: Voice Mode
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [08_voice_buddy.md]
tags: [feature, voice, speech-to-text]
---

# Voice Mode

Hold-to-talk voice input in [[claude-code]]: hold shortcut → record → WebSocket STT → text injection.

## Definition

Voice mode converts speech to text using Deepgram Nova 3 via WebSocket. Three-layer availability check: compile-time feature gate → OAuth authentication → GrowthBook kill-switch. Records immediately before WebSocket connects, buffering audio to eliminate 1-2s delay.

## Key Design Decisions

- **Record first, connect later**: buffers audio during WebSocket handshake — every word captured
- **api.anthropic.com over claude.ai**: bypasses Cloudflare TLS fingerprint (JA3) detection for CLI
- **Silent drop detection**: `no_data_timeout` catches ~1% sticky bad pod failures; retries with full audio replay
- **Auto-repeat key detection**: terminals can't detect key release, so OS key repeat events are used as proxy
- **Kill-switch default false**: new installs work without waiting for GrowthBook init

## Related Concepts

- [[feature-flags]]
- [[buddy-system]]

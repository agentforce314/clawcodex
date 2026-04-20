# Chapter 8: Voice Mode & Buddy System

> This chapter covers two distinctive features of Claude Code: the voice input system and the virtual companion system.
> They demonstrate how a CLI tool can incorporate creative features while maintaining professionalism.

## Learning Objectives
1. Understand Voice mode's complete tech stack (hold-to-talk → recording → WebSocket STT → injection)
2. Understand Voice's multi-layer availability checks (feature gate + OAuth + GrowthBook kill-switch)
3. Understand the Buddy system's deterministic generation mechanism (hash → PRNG → bones)
4. Understand Buddy's ASCII sprite animation system
5. Understand Buddy's prompt injection and collaboration with Claude

---

## 8.1 Voice Mode — Hold-to-Talk Voice Input

**Building intuition first**: Voice mode lets you hold a shortcut key and speak — Claude Code converts your speech to text input. The full flow is: hold key → record audio → send via WebSocket to speech recognition service → recognized text auto-fills the input box. But this feature isn't available to everyone — it must pass three layers of checks (compile-time feature gate, OAuth authentication, remote kill-switch), and failing any layer disables it.

### 8.1.1 Tech Stack Overview

```
User holds shortcut key
  │
  ▼
┌──────────────────┐
│ useVoice.ts      │  React Hook, manages recording state machine
│ (hold-to-talk)   │
└────────┬─────────┘
         │ Audio data (PCM 16-bit, 16kHz, mono)
         ▼
┌──────────────────┐     ┌──────────────────────┐
│ Recording Backend │     │ voiceStreamSTT.ts     │
│ ├── native module │────▶│ WebSocket client       │
│ │   (macOS)       │     │ → Anthropic voice_stream│
│ └── SoX fallback  │     │ → Deepgram Nova 3 STT  │
└──────────────────┘     └──────────┬───────────┘
                                    │ TranscriptText / TranscriptEndpoint
                                    ▼
                          ┌──────────────────┐
                          │ Text injection    │
                          │ onTranscript()    │
                          └──────────────────┘
```

### 8.1.2 Three-Layer Availability Check

```typescript
// voiceModeEnabled.ts

// Layer 1: Feature gate (compile-time)
// Only ant builds include VOICE_MODE feature
function isVoiceGrowthBookEnabled(): boolean {
  return feature('VOICE_MODE')
    ? !getFeatureValue_CACHED_MAY_BE_STALE('tengu_amber_quartz_disabled', false)
    : false
}

// Layer 2: OAuth authentication check
// Voice uses claude.ai's voice_stream endpoint, requires OAuth token
function hasVoiceAuth(): boolean {
  if (!isAnthropicAuthEnabled()) return false
  const tokens = getClaudeAIOAuthTokens()
  return Boolean(tokens?.accessToken)
}

// Layer 3: Complete runtime check
export function isVoiceModeEnabled(): boolean {
  return hasVoiceAuth() && isVoiceGrowthBookEnabled()
}
```

**Design Insight 1: GrowthBook Kill-Switch Default Value Design**

```typescript
// Source comment:
// Default `false` means a missing/stale disk cache reads as "not killed"
// — so fresh installs get voice working immediately without waiting for
// GrowthBook init.
!getFeatureValue_CACHED_MAY_BE_STALE('tengu_amber_quartz_disabled', false)
```

The kill-switch defaults to `false` (not disabled), meaning:
- New installs don't need to wait for GrowthBook initialization to use voice
- Only when Anthropic actively pushes `true` will it be disabled
- This is a "default on, emergency off" pattern

### 8.1.3 WebSocket Connection & Audio Transmission

```typescript
// voiceStreamSTT.ts
export async function connectVoiceStream(
  callbacks: VoiceStreamCallbacks,
  options?: { language?: string; keyterms?: string[] },
): Promise<VoiceStreamConnection | null> {
  // Refresh OAuth token
  await checkAndRefreshOAuthTokenIfNeeded()

  // Connect to api.anthropic.com (NOT claude.ai)
  // Reason: claude.ai's Cloudflare uses TLS fingerprint detection,
  // which challenges non-browser clients
  const wsBaseUrl = getOauthConfig()
    .BASE_API_URL.replace('https://', 'wss://')
    .replace('http://', 'ws://')

  const params = new URLSearchParams({
    encoding: 'linear16',
    sample_rate: '16000',
    channels: '1',
    endpointing_ms: '300',    // 300ms silence → endpoint detection
    utterance_end_ms: '1000', // 1s silence → utterance end
    language: options?.language ?? 'en',
  })

  // Deepgram Nova 3 routing (via conversation-engine)
  if (isNova3GateEnabled) {
    params.set('use_conversation_engine', 'true')
    params.set('stt_provider', 'deepgram-nova3')
  }

  // Keyterms boosting (improve recognition accuracy for specific terms)
  if (options?.keyterms?.length) {
    for (const term of options.keyterms) {
      params.append('keyterms', term)
    }
  }

  const ws = new WebSocket(url, wsOptions)
  // ...
}
```

**Design Insight 2: Why api.anthropic.com instead of claude.ai?**

```
claude.ai's Cloudflare zone uses TLS fingerprint detection (JA3 fingerprint).
The CLI's WebSocket client isn't a browser and gets challenged.

api.anthropic.com is the same private-api pod, same OAuth Bearer auth,
but the CF zone doesn't block non-browser clients.

The Desktop version still uses claude.ai (Swift URLSession has browser-level JA3 fingerprint).
```

### 8.1.4 Recording & Buffering Strategy

```typescript
// useVoice.ts — startRecordingSession

// Key design: recording starts immediately, doesn't wait for WebSocket connection
// Audio is buffered first, flushed when WebSocket is ready
const audioBuffer: Buffer[] = []

// Start recording immediately
const started = await voiceModule.startRecording(
  (chunk: Buffer) => {
    const owned = Buffer.from(chunk)  // Copy! Native module may reuse buffer
    if (connectionRef.current) {
      connectionRef.current.send(owned)  // WebSocket connected → send directly
    } else {
      audioBuffer.push(owned)  // Not connected → buffer
    }
  },
)

// Flush buffer when WebSocket is ready
onReady: conn => {
  // Merge into ~1s slices to reduce WS frame count
  const SLICE_TARGET_BYTES = 32_000  // 32KB ≈ 1s of 16kHz 16-bit mono
  const slices: Buffer[][] = [[]]
  for (const chunk of audioBuffer) {
    if (sliceBytes + chunk.length > SLICE_TARGET_BYTES) {
      slices.push([])
      sliceBytes = 0
    }
    slices[slices.length - 1]!.push(chunk)
    sliceBytes += chunk.length
  }
  for (const slice of slices) {
    conn.send(Buffer.concat(slice))
  }
  audioBuffer.length = 0
}
```

**Design Insight 3: Record First, Connect Later**

```
Traditional approach:
  Key press → Connect WebSocket (1-2s) → Start recording
  First 1-2 seconds of speech are lost

Claude Code's approach:
  Key press → Start recording immediately + Connect WebSocket simultaneously
  WebSocket ready → Flush buffered audio
  Every word the user says is captured
```

### 8.1.5 Finalize & Silent Drop Detection

```typescript
// When user releases the key
finalize(): Promise<FinalizeSource> {
  return new Promise<FinalizeSource>(resolve => {
    // Safety timeout: 5s (last resort for hung WebSocket)
    const safetyTimer = setTimeout(
      () => resolveFinalize?.('safety_timeout'),
      5_000,
    )

    // No-data timeout: 1.5s (server returned no transcript)
    const noDataTimer = setTimeout(
      () => resolveFinalize?.('no_data_timeout'),
      1_500,
    )

    // Delay sending CloseStream (let event loop drain audio callbacks)
    setTimeout(() => {
      ws.send('{"type":"CloseStream"}')
    }, 0)
  })
}
```

**Design Insight 4: no_data_timeout Detects Silent Drops**

```
~1% of sessions encounter a "sticky bad pod":
  - CE (conversation-engine) pod accepts audio but returns no transcript
  - This is a session-sticky variant of anthropics/anthropic#287008

Detection:
  finalize() resolves via no_data_timeout
  + hadAudioSignal = true (there was actual sound)
  → Classified as silent drop

Recovery:
  Replay full audio to a new WebSocket connection (one retry chance)
  fullAudioRef stores all audio chunks (~32KB/s × 60s ≈ 2MB)
```

### 8.1.6 Auto-Repeat Key Detection

```
Hold-to-talk challenge: terminals can't directly detect "key release".

Solution: leverage OS key repeat mechanism
  1. User holds shortcut key
  2. OS starts sending repeat key events after ~500ms delay
  3. useVoice detects repeat event → sets seenRepeat = true
  4. Starts RELEASE_TIMEOUT_MS timer
  5. If timer expires without new key event → classified as released
  6. Stop recording, send finalize

Key: timer only starts after detecting repeat,
avoiding false release detection during OS key repeat delay.
```

---

## 8.2 Buddy System — Virtual Companion

### 8.2.1 System Overview

Buddy is Claude Code's virtual pet/companion system. Users can "hatch" a companion via the `/buddy` command. It displays an ASCII sprite animation next to the input box and occasionally comments in a speech bubble.

```
┌─────────────────────────────────────────────────────────┐
│  Buddy System Architecture                               │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  companion.ts    → Deterministic generation (hash→PRNG→bones) │
│  types.ts        → Type definitions (species, rarity, stats)  │
│  sprites.ts      → ASCII animation frames (18 species × 3)    │
│  prompt.ts       → Prompt injection (tell Claude about buddy) │
│  CompanionSprite → React component (render + animation loop)  │
│  useBuddyNotification → Rainbow /buddy prompt                 │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 8.2.2 Deterministic Generation — hash(userId) → Companion

Each user's companion is **deterministic** — the same userId always generates the same companion:

```typescript
// companion.ts
const SALT = 'friend-2026-401'

export function roll(userId: string): Roll {
  const key = userId + SALT
  // Cache: only compute once per userId
  if (rollCache?.key === key) return rollCache.value
  const value = rollFrom(mulberry32(hashString(key)))
  rollCache = { key, value }
  return value
}
```

**Design Insight 5: Mulberry32 PRNG**

```typescript
// A minimal 32-bit seeded PRNG, good enough for picking ducks
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
```

The comment says it plainly: "tiny seeded PRNG, good enough for picking ducks". No need for cryptographic randomness — just determinism and uniform distribution.

### 8.2.3 Bones vs Soul — Separated Persistence Strategy

```typescript
// Bones — deterministic part, generated from hash(userId), NOT persisted
type CompanionBones = {
  rarity: Rarity       // common(60%) / uncommon(25%) / rare(10%) / epic(4%) / legendary(1%)
  species: Species     // 18 species
  eye: Eye             // 6 eye styles
  hat: Hat             // 8 hats (common has no hat)
  shiny: boolean       // 1% chance of shiny
  stats: Record<StatName, number>  // DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK
}

// Soul — model-generated part, persisted to config
type CompanionSoul = {
  name: string         // Name given by model
  personality: string  // Personality description generated by model
}

// Only Soul + hatchedAt is stored
type StoredCompanion = CompanionSoul & { hatchedAt: number }
```

**Design Insight 6: Why Bones Are Not Persisted**

```
// Source comment:
// Bones never persist so species renames and SPECIES-array edits can't
// break stored companions, and editing config.companion can't fake a rarity.

Benefits:
1. If developers rename a species → won't break existing companions
2. If SPECIES array is adjusted → won't cause deserialization errors
3. Users can't fake legendary rarity by editing config files
4. Regenerated on each read → always consistent with latest code
```

### 8.2.4 Species Name Encoding Trick

```typescript
// types.ts — Why are species names encoded with charCode?
const c = String.fromCharCode
export const duck = c(0x64,0x75,0x63,0x6b) as 'duck'
export const goose = c(0x67,0x6f,0x6f,0x73,0x65) as 'goose'
// ...
```

**Design Insight 7: Bypassing Build Checks**

```
// Source comment:
// One species name collides with a model-codename canary in excluded-strings.txt.
// The check greps build output (not source), so runtime-constructing the value
// keeps the literal out of the bundle while the check stays armed for the
// actual codename.

Anthropic's build system checks output for model codenames (to prevent leaks).
One species name happens to collide with a model codename.
Using charCode to construct strings → no literal in build output → check passes.
All species use this encoding uniformly for consistency.
```

### 8.2.5 ASCII Sprite Animation System

Each species has 3 animation frames, each 5 lines × 12 characters wide:

```
Frame 0 (idle):        Frame 1 (fidget):      Frame 2 (special):
    __                    __                    __
  <(· )___              <(· )___              <(· )___
   (  ._>                (  ._>                (  .__>
    `--´                  `--´~                 `--´
```

```typescript
// sprites.ts
export function renderSprite(bones: CompanionBones, frame = 0): string[] {
  const frames = BODIES[bones.species]
  const body = frames[frame % frames.length]!.map(line =>
    line.replaceAll('{E}', bones.eye),  // Replace eye placeholder
  )
  const lines = [...body]

  // Hat only shows when line 0 is empty (some frames use line 0 for smoke/antenna effects)
  if (bones.hat !== 'none' && !lines[0]!.trim()) {
    lines[0] = HAT_LINES[bones.hat]
  }

  // If all frames have empty line 0 and no hat → remove empty line to save space
  if (!lines[0]!.trim() && frames.every(f => !f[0]!.trim())) lines.shift()

  return lines
}
```

**Design Insight 8: Hat vs Animation Frame Conflict Handling**

```
Frame 0: empty line → can place hat
Frame 1: empty line → can place hat
Frame 2: "   ~    ~   " → smoke effect, can't place hat

Solution: only replace hat when lines[0].trim() is empty.
This way dragon's frame 2 can show smoke instead of hat.
```

### 8.2.6 Stats Generation — One Peak, One Dump

```typescript
function rollStats(rng: () => number, rarity: Rarity): Record<StatName, number> {
  const floor = RARITY_FLOOR[rarity]  // common:5, uncommon:15, rare:25, epic:35, legendary:50
  const peak = pick(rng, STAT_NAMES)
  let dump = pick(rng, STAT_NAMES)
  while (dump === peak) dump = pick(rng, STAT_NAMES)  // Ensure different

  const stats = {} as Record<StatName, number>
  for (const name of STAT_NAMES) {
    if (name === peak) {
      stats[name] = Math.min(100, floor + 50 + Math.floor(rng() * 30))
    } else if (name === dump) {
      stats[name] = Math.max(1, floor - 10 + Math.floor(rng() * 15))
    } else {
      stats[name] = floor + Math.floor(rng() * 40)
    }
  }
  return stats
}
```

This creates companions with personality: each has one outstanding strength and one notable weakness, rather than all mediocre stats. Higher rarity means higher base values.

### 8.2.7 Prompt Injection — How Claude Coexists with Buddy

```typescript
// prompt.ts
export function companionIntroText(name: string, species: string): string {
  return `# Companion

A small ${species} named ${name} sits beside the user's input box and
occasionally comments in a speech bubble. You're not ${name} — it's a
separate watcher.

When the user addresses ${name} directly (by name), its bubble will answer.
Your job in that moment is to stay out of the way: respond in ONE line or
less, or just answer any part of the message meant for you. Don't explain
that you're not ${name} — they know. Don't narrate what ${name} might say
— the bubble handles that.`
}
```

**Design Insight 9: Role Separation Prompt Design**

```
Key instructions:
1. "You're not {name}" → Clearly separate Claude and Buddy as different entities
2. "stay out of the way" → Claude yields when user talks to Buddy
3. "Don't explain that you're not {name}" → Prevent Claude from breaking immersion
4. "Don't narrate what {name} might say" → Prevent Claude from speaking for Buddy

This is a carefully designed "coexistence protocol":
  Claude handles technical work
  Buddy handles emotional interaction
  Neither interferes with the other
```

### 8.2.8 Teaser Window — Time-Limited Discovery Mechanism

```typescript
// useBuddyNotification.tsx

// Local time (not UTC) — 24h rolling wave across timezones
// Sustained Twitter buzz instead of a single UTC-midnight spike
// Teaser window: April 1-7, 2026 only. Command permanently available after.
export function isBuddyTeaserWindow(): boolean {
  if ("external" === 'ant') return true  // Always available for Anthropic internal
  const d = new Date()
  return d.getFullYear() === 2026 && d.getMonth() === 3 && d.getDate() <= 7
}

export function isBuddyLive(): boolean {
  if ("external" === 'ant') return true
  const d = new Date()
  return d.getFullYear() > 2026 || (d.getFullYear() === 2026 && d.getMonth() >= 3)
}
```

**Design Insight 10: Local Time Instead of UTC**

```
// Source comment:
// Local date, not UTC — 24h rolling wave across timezones. Sustained Twitter
// buzz instead of a single UTC-midnight spike, gentler on soul-gen load.

If using UTC:
  UTC midnight → global simultaneous discovery → single Twitter spike → soul-gen server overload

Using local time:
  Each timezone's midnight → gradual spread over 24h → sustained social media buzz → smooth server load
```

At startup, if the user doesn't have a companion and is within the teaser window, a rainbow-colored `/buddy` prompt appears:

```typescript
addNotification({
  key: 'buddy-teaser',
  jsx: <RainbowText text="/buddy" />,
  priority: 'immediate',
  timeoutMs: 15_000,  // Disappears after 15 seconds
})
```

### 8.2.9 Caching Strategy

```typescript
// companion.ts
// Called from three hot paths (500ms sprite tick, every keystroke PromptInput, every turn observer)
// Same userId → cache deterministic result
let rollCache: { key: string; value: Roll } | undefined
export function roll(userId: string): Roll {
  const key = userId + SALT
  if (rollCache?.key === key) return rollCache.value
  const value = rollFrom(mulberry32(hashString(key)))
  rollCache = { key, value }
  return value
}
```

A single-entry cache is sufficient since userId doesn't change within the same process.

---

## Chapter Summary

### Voice Mode

| # | Design Insight | Problem Solved |
|---|---------------|----------------|
| 1 | Kill-switch defaults to false | New installs work without waiting for GrowthBook |
| 2 | api.anthropic.com | Bypasses claude.ai's TLS fingerprint detection |
| 3 | Record first, connect later | Eliminates 1-2s WebSocket connection delay |
| 4 | no_data_timeout | Detects and recovers from silent drops (~1% of sessions) |

### Buddy System

| # | Design Insight | Problem Solved |
|---|---------------|----------------|
| 5 | Mulberry32 PRNG | Deterministic generation, "good enough for picking ducks" |
| 6 | Bones not persisted | Prevents species renames from breaking saves, prevents rarity forgery |
| 7 | charCode-encoded species | Bypasses build system's model codename check |
| 8 | Hat vs animation frame conflict | Smoke/antenna frames not overwritten by hats |
| 9 | Role separation prompt | Claude and Buddy each do their job without interference |
| 10 | Local time teaser | 24h rolling wave, smooth server load and social media buzz |

/**
 * Working indicator, faithful to the original Claude Code spinner
 * (typescript/src/components/Spinner.tsx + Spinner/SpinnerGlyph + GlimmerMessage):
 *
 *   ✻ Cogitating… (3s · esc to interrupt)
 *
 *  - an oscillating glyph cycle `· ✢ ✳ ✶ ✻ ✽` (then reversed) in Claude orange,
 *    advancing every 120ms (SpinnerGlyph's SPINNER_FRAMES);
 *  - a random whimsical verb chosen once per turn (SPINNER_VERBS);
 *  - a glimmer: a brighter highlight sweeps across the verb (GlimmerMessage);
 *  - a dim `(elapsed · esc to interrupt)` status.
 */
import { Text } from 'ink'
import React, { useEffect, useState } from 'react'
import { theme } from '../theme.js'
import { SPINNER_VERBS } from '../spinnerVerbs.js'

/** Glyph cycle — matches getDefaultCharacters() per-platform, then oscillates. */
function frameChars(): string[] {
  if (process.env['TERM'] === 'xterm-ghostty') return ['·', '✢', '✳', '✶', '✻', '*']
  return process.platform === 'darwin'
    ? ['·', '✢', '✳', '✶', '✻', '✽']
    : ['·', '✢', '*', '✶', '✻', '✽']
}
const CHARS = frameChars()
const FRAMES = [...CHARS, ...[...CHARS].reverse()]
const TICK_MS = 50
const FRAME_MS = 120 // glyph advance cadence (original: floor(time/120))
const GLIMMER_MS = 200 // glimmer sweep step

function pickVerb(): string {
  return SPINNER_VERBS[Math.floor(Math.random() * SPINNER_VERBS.length)] ?? 'Working'
}

function fmtElapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${s % 60}s`
}

export function Spinner({
  startedAt,
  activity,
}: {
  startedAt: number
  /** Live tool activity (e.g. "Reading 3 files") — shown instead of the verb. */
  activity?: string | null
}): React.ReactElement {
  const [time, setTime] = useState(0)
  const [verb] = useState(pickVerb)

  useEffect(() => {
    const id = setInterval(() => setTime((t) => t + TICK_MS), TICK_MS)
    return () => clearInterval(id)
  }, [])

  const frame = Math.floor(time / FRAME_MS) % FRAMES.length
  const message = activity || verb
  const chars = [...message]

  // Glimmer: a brighter 3-char window sweeps right→left across the verb, with a
  // gap between sweeps (matches GlimmerMessage's `width + 10 - pos % cycle`).
  const cycleLength = chars.length + 20
  const glimmerIndex = chars.length + 10 - (Math.floor(time / GLIMMER_MS) % cycleLength)

  return (
    <Text>
      <Text color={theme.spinner}>{FRAMES[frame]} </Text>
      {chars.map((ch, i) => {
        const lit = Math.abs(i - glimmerIndex) <= 1
        return (
          <Text key={i} color={lit ? theme.spinnerShimmer : theme.spinner} bold={lit}>
            {ch}
          </Text>
        )
      })}
      <Text color={theme.spinner}>… </Text>
      <Text color={theme.dim}>{`(${fmtElapsed(Date.now() - startedAt)} · esc to interrupt)`}</Text>
    </Text>
  )
}

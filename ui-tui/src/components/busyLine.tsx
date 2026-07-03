/**
 * The original Claude Code busy line (Spinner.tsx / SpinnerAnimationRow.tsx),
 * rendered directly above the composer when the opt-in status bar is off:
 *
 *   ✻ Extracting the loader… (1m 3s · ↓ ~1.2k tokens · thinking…)
 *   Next: Add unit tests for env overrides
 *
 * - Star ping-pong glyph `· ✢ ✳ ✶ ✻ ✽ ✽ ✻ ✶ ✳ ✢ ·` @120ms in claude orange.
 * - Verb = in-progress todo's activeForm ?? subject ?? a per-turn pick from
 *   the brand VERBS list, with a 3-column claudeShimmer band sweeping
 *   right→left @200ms (GlimmerMessage).
 * - Dim parenthetical appears only after 30s (SHOW_TOKENS_AFTER_MS): elapsed
 *   and `↓ ~N tokens` (chars/4 — an estimate, hence the ~). `thinking…`
 *   rides along while reasoning streams.
 * - Stall: >3s without any delta (and no running tools) cuts glyph+verb to
 *   the original's hardcoded rgb(171,43,63).
 * - Right-aligned dim delegation segment (depth/⚡/⛓) only during fan-out —
 *   deliberate delta from the original's bare row (that signal is
 *   load-bearing here).
 */
import { Box, stringWidth, Text } from '@clawcodex/ink'
import { useStore } from '@nanostores/react'
import { memo, useEffect, useMemo, useState } from 'react'

import { $delegationState } from '../app/delegationStore.js'
import { useTurnSelector } from '../app/turnStore.js'
import { $uiState } from '../app/uiStore.js'
import { VERBS } from '../content/verbs.js'
import { fmtDuration } from '../domain/messages.js'
import { buildSubagentTree, treeTotals } from '../lib/subagentTree.js'
import { fmtK } from '../lib/text.js'
import type { Theme } from '../theme.js'

const STAR_FRAMES = ['·', '✢', '✳', '✶', '✻', '✽', '✽', '✻', '✶', '✳', '✢', '·']
const GLYPH_TICK_MS = 120
const SHIMMER_TICK_MS = 200
const SHIMMER_BAND = 3
const SHOW_SUFFIX_AFTER_MS = 30_000
const STALL_AFTER_MS = 3_000
// Original useStalledAnimation ERROR_RED — deliberately NOT theme.error.
const STALL_RED = 'rgb(171,43,63)'

/** Verb with a claudeShimmer band sweeping right→left (GlimmerMessage-lite). */
function ShimmerVerb({ stalled, t, tick, verb }: { stalled: boolean; t: Theme; tick: number; verb: string }) {
  if (stalled) {
    return <Text color={STALL_RED}>{verb}</Text>
  }

  const chars = [...verb]
  const period = chars.length + SHIMMER_BAND
  const head = period - 1 - (tick % period) // right→left sweep

  return (
    <Text>
      {chars.map((ch, i) => (
        <Text color={i >= head && i < head + SHIMMER_BAND ? t.color.claudeShimmer : t.color.accent} key={i}>
          {ch}
        </Text>
      ))}
    </Text>
  )
}

export const BusyLine = memo(function BusyLine({ t, turnStartedAt }: BusyLineProps) {
  const ui = useStore($uiState)
  const todos = useTurnSelector(state => state.todos)
  const tools = useTurnSelector(state => state.tools)
  const streamedChars = useTurnSelector(state => state.streamedChars)
  const lastDeltaAt = useTurnSelector(state => state.lastDeltaAt)
  const reasoningStreaming = useTurnSelector(state => state.reasoningStreaming)
  const subagents = useTurnSelector(state => state.subagents)
  const delegation = useStore($delegationState)

  const [tick, setTick] = useState(0)
  const [now, setNow] = useState(() => Date.now())

  // One random verb per turn (original sample()-on-mount).
  const fallbackVerb = useMemo(
    () => VERBS[Math.floor(Math.random() * VERBS.length)] ?? 'working',
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [turnStartedAt]
  )

  useEffect(() => {
    if (!ui.busy) {
      return
    }

    const glyph = setInterval(() => setTick(n => n + 1), GLYPH_TICK_MS)
    const clock = setInterval(() => setNow(Date.now()), 1000)

    return () => {
      clearInterval(glyph)
      clearInterval(clock)
    }
  }, [ui.busy])

  if (!ui.busy) {
    return null
  }

  const activeTodo = todos.find(todo => todo.status === 'in_progress')
  const verb = `${activeTodo?.activeForm ?? activeTodo?.content ?? fallbackVerb}…`

  const glyph = STAR_FRAMES[tick % STAR_FRAMES.length] ?? '✻'
  const shimmerTick = Math.floor((tick * GLYPH_TICK_MS) / SHIMMER_TICK_MS)

  const elapsedMs = turnStartedAt ? now - turnStartedAt : 0
  const stalled = tools.length === 0 && lastDeltaAt !== null && now - lastDeltaAt > STALL_AFTER_MS

  // Suffix parts appear progressively after 30s (SHOW_TOKENS_AFTER_MS parity).
  const parts: string[] = []

  if (elapsedMs > SHOW_SUFFIX_AFTER_MS) {
    parts.push(fmtDuration(elapsedMs))

    const tokens = Math.round(streamedChars / 4)

    if (tokens > 0) {
      parts.push(`↓ ~${fmtK(tokens)} tokens`)
    }
  }

  if (reasoningStreaming) {
    parts.push('thinking…')
  }

  // Delegation segment (right-aligned, dim) only while fanning out.
  const tree = buildSubagentTree(subagents)
  const totals = treeTotals(tree)
  const delegating = totals.descendantCount > 0 || delegation.paused

  const delegationLabel = !delegating
    ? ''
    : totals.descendantCount === 0
      ? '⏸ paused'
      : `${delegation.paused ? '⏸ ' : ''}⛓ ${totals.activeCount > 0 ? `${totals.activeCount} running` : `${totals.descendantCount} spawned`}`

  const nextTodo = todos.find(todo => todo.status === 'pending')

  return (
    <Box flexDirection="column" marginTop={1}>
      <Box justifyContent="space-between">
        <Text>
          <Text color={stalled ? STALL_RED : t.color.accent}>{glyph} </Text>
          <ShimmerVerb stalled={stalled} t={t} tick={shimmerTick} verb={verb} />
          {parts.length > 0 && (
            <Text color={t.color.muted} dim>
              {' ('}
              {parts.join(' · ')}
              {')'}
            </Text>
          )}
        </Text>
        {delegationLabel ? (
          <Text color={t.color.muted} dim>
            {delegationLabel}
          </Text>
        ) : null}
      </Box>
      {nextTodo && stringWidth(nextTodo.content) > 0 ? (
        <Text color={t.color.muted} dim>
          Next: {nextTodo.content}
        </Text>
      ) : null}
    </Box>
  )
})

interface BusyLineProps {
  t: Theme
  turnStartedAt: null | number
}

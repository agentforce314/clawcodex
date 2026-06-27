/**
 * Animated working indicator: braille frames + a (stable, per-turn) verb +
 * elapsed seconds + an interrupt hint — the Claude-Code "Working… (3s · esc)".
 */
import { Text } from 'ink'
import React, { useEffect, useState } from 'react'
import { theme } from '../theme.js'

const FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
const VERBS = [
  'Thinking',
  'Working',
  'Brewing',
  'Concocting',
  'Pondering',
  'Computing',
  'Noodling',
  'Spelunking',
  'Percolating',
  'Conjuring',
]

export function Spinner({
  startedAt,
  activity,
}: {
  startedAt: number
  /** Live tool activity (e.g. "Reading 3 files") — shown instead of the verb. */
  activity?: string | null
}): React.ReactElement {
  const [frame, setFrame] = useState(0)
  const [verb] = useState(() => VERBS[Math.floor(Math.random() * VERBS.length)])
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const anim = setInterval(() => setFrame((f) => (f + 1) % FRAMES.length), 90)
    const clock = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000)
    return () => {
      clearInterval(anim)
      clearInterval(clock)
    }
  }, [startedAt])

  return (
    <Text color={theme.spinner}>
      {FRAMES[frame]} {activity || verb}…{' '}
      <Text color={theme.dim}>({elapsed}s · esc to interrupt)</Text>
    </Text>
  )
}

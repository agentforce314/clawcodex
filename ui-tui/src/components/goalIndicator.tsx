/**
 * GoalIndicator — the persistent "a /goal is driving this session" badge,
 * right-aligned directly above the composer (reference: Claude Code's
 * `◎ /goal active (14s)` chrome line):
 *
 *   ◎ /goal active (14s)                ← permission lavender, elapsed ticks 1s
 *   ◎ /goal active (3m 2s · turn 3/20)  ← turn odometer once the judge has run
 *   ⏸ /goal paused · /goal resume       ← muted (budget/ESC/user pause)
 *
 * Unlike BusyLine it renders while idle too: the loop's judge window (turn
 * ended, evaluator deciding) is exactly when the user needs the reassurance
 * that the goal is still armed. Elapsed comes off the backend's created_at,
 * so pause→resume keeps the original clock and a restore starts a fresh one.
 */
import { Box, Text } from '@clawcodex/ink'
import { useStore } from '@nanostores/react'
import { memo, useEffect, useState } from 'react'

import { $goalState } from '../app/goalStore.js'
import { fmtDuration } from '../domain/messages.js'
import type { Theme } from '../theme.js'

export const GoalIndicator = memo(function GoalIndicator({ t }: { t: Theme }) {
  const goal = useStore($goalState)
  const [now, setNow] = useState(() => Date.now())

  const active = goal?.status === 'active'

  useEffect(() => {
    if (!active) {
      return
    }

    setNow(Date.now()) // repaint immediately on (re)activation, then tick
    const clock = setInterval(() => setNow(Date.now()), 1000)

    return () => clearInterval(clock)
  }, [active])

  if (!goal) {
    return null
  }

  if (goal.status === 'paused') {
    return (
      <Box justifyContent="flex-end">
        <Text color={t.color.muted} dim>
          ⏸ /goal paused · /goal resume
        </Text>
      </Box>
    )
  }

  const elapsed = fmtDuration(Math.max(0, now - goal.startedAt))
  const turns = goal.turnsUsed > 0 && goal.maxTurns > 0 ? ` · turn ${goal.turnsUsed}/${goal.maxTurns}` : ''

  return (
    <Box justifyContent="flex-end">
      <Text color={t.color.permission}>
        ◎ /goal active ({elapsed}
        {turns})
      </Text>
    </Box>
  )
})

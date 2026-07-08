/**
 * CronIndicator — the persistent "scheduled tasks are armed" badge,
 * right-aligned directly above the composer next to GoalIndicator
 * (reference: Claude Code's scheduled-task chrome):
 *
 *   ⟳ loop wakeup in 2m 14s              ← pending self-paced /loop wakeup
 *   ⏰ 2 scheduled · next in 4m 32s      ← cron jobs, soonest fire counts down
 *   ⟳ loop wakeup in 58s · ⏰ 1 scheduled ← both armed
 *
 * Like GoalIndicator it renders while idle — the wait BETWEEN loop
 * iterations is exactly when the user needs to see that the loop is still
 * armed (and that Esc will clear the pending wakeup). Countdown ticks off
 * the backend's fire_at epochs; "due" shows once a fire time passes (the
 * worker pops it within its 0.5s idle poll).
 */
import { Box, Text } from '@clawcodex/ink'
import { useStore } from '@nanostores/react'
import { memo, useEffect, useState } from 'react'

import { $cronState } from '../app/cronStore.js'
import { fmtDuration } from '../domain/messages.js'
import type { Theme } from '../theme.js'

const untilText = (fireAt: number, now: number) => {
  const delta = fireAt - now

  return delta <= 500 ? 'due' : `in ${fmtDuration(delta)}`
}

export const CronIndicator = memo(function CronIndicator({ t }: { t: Theme }) {
  const cron = useStore($cronState)
  const [now, setNow] = useState(() => Date.now())

  const armed = cron !== null

  useEffect(() => {
    if (!armed) {
      return
    }

    setNow(Date.now()) // repaint immediately on (re)arm, then tick
    const clock = setInterval(() => setNow(Date.now()), 1000)

    return () => clearInterval(clock)
  }, [armed])

  if (!cron) {
    return null
  }

  const parts: string[] = []

  if (cron.wakeup) {
    const label = cron.wakeup.isFallback ? 'loop fallback' : 'loop wakeup'

    parts.push(`⟳ ${label} ${untilText(cron.wakeup.fireAt, now)}`)
  }

  if (cron.jobCount > 0) {
    const next = cron.nextJobAt === null ? '' : ` · next ${untilText(cron.nextJobAt, now)}`

    parts.push(`⏰ ${cron.jobCount} scheduled${next}`)
  }

  if (!parts.length) {
    return null
  }

  return (
    <Box justifyContent="flex-end">
      <Text color={t.color.muted}>{parts.join(' · ')}</Text>
    </Box>
  )
})

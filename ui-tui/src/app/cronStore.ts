import { atom } from 'nanostores'

import type { CronSnapshot } from '../gatewayTypes.js'

// Scheduled-task indicator state — feeds the persistent right-aligned
// "⟳ loop wakeup in 2m 14s" / "⏰ 2 scheduled · next 4m" line above the
// composer (CronIndicator). The backend is the single source of truth:
// every cron_status system event carries a full `scheduled` snapshot
// (jobs + pending /loop wakeup), folded in here. Prompts arrive as
// previews only; CronList is the reading surface for full detail.

export interface CronIndicatorState {
  /** Number of scheduled cron jobs (recurring + one-shot). */
  jobCount: number
  /** Epoch ms of the soonest job fire, or null when no jobs. */
  nextJobAt: null | number
  /** Pending dynamic-loop wakeup, or null. */
  wakeup: null | { fireAt: number; isFallback: boolean; reason: string }
}

export const $cronState = atom<CronIndicatorState | null>(null)

export const getCronState = () => $cronState.get()

export const resetCronState = () => {
  $cronState.set(null)
}

const toMs = (value: unknown): null | number => {
  const n = Number(value)

  // Epoch SECONDS on the wire (python time.time()); garbage hides the entry.
  return Number.isFinite(n) && n > 0 ? Math.round(n * 1000) : null
}

/** Fold a wire snapshot into the store. An empty snapshot (no jobs, no
 *  wakeup) hides the indicator. */
export const applyCronSnapshot = (snap: CronSnapshot | null | undefined) => {
  if (!snap || typeof snap !== 'object') {
    $cronState.set(null)

    return
  }

  const jobs = Array.isArray(snap.jobs) ? snap.jobs : []
  const fireTimes = jobs.map(j => toMs(j?.next_fire_at)).filter((t): t is number => t !== null)
  const rawWakeup = snap.wakeup
  const wakeupAt = rawWakeup ? toMs(rawWakeup.fire_at) : null

  if (!jobs.length && wakeupAt === null) {
    $cronState.set(null)

    return
  }

  $cronState.set({
    jobCount: jobs.length,
    nextJobAt: fireTimes.length ? Math.min(...fireTimes) : null,
    wakeup:
      wakeupAt === null
        ? null
        : {
            fireAt: wakeupAt,
            isFallback: Boolean(rawWakeup?.is_fallback),
            reason: String(rawWakeup?.reason ?? '')
          }
  })
}

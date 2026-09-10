import { Fragment } from 'react'

import { formatTokens, type TrajectoryStats } from '../state/trajectory.ts'
import css from './StatsLine.module.css'

export interface StatsLineProps {
  stats: TrajectoryStats
}

/**
 * The three disjoint prompt-side buckets the run was billed for.
 *
 * The same sum `cacheHitRatio` divides into, so the percentage and the token
 * figure beside it describe one arithmetic rather than two.
 */
export function billedInput(stats: TrajectoryStats): number {
  return stats.uncachedInputTokens + stats.cacheReadTokens + stats.cacheWriteTokens
}

/**
 * A cache-hit share that never rounds a partial hit up to a full one.
 *
 * `100%` has to mean every prompt token came from cache; a 99.7% run reading
 * `100%` is the one reading a person would act on differently. Precision
 * escalates until the figure stays under 100, and a genuine full hit is `100`.
 */
export function cacheHitPercent(ratio: number): string {
  if (ratio >= 1) return '100'

  for (const places of [0, 1, 2, 3]) {
    const shown = (ratio * 100).toFixed(places)

    if (Number(shown) < 100) return shown
  }

  return '99.999'
}

/** Output speed: the digit matters below 10, where `0 tok/s` would be a lie. */
export function formatSpeed(tps: number): string {
  return tps >= 10 ? tps.toFixed(0) : String(Math.round(tps * 10) / 10)
}

/**
 * A duration at the width the stats line affords: `23.7s` under a minute,
 * `6m28s` from there, `2h05m` past an hour.
 */
export function formatCompactDuration(ms: number): string {
  const seconds = Math.max(0, ms) / 1000

  if (seconds < 60) return `${Math.round(seconds * 10) / 10}s`

  const whole = Math.round(seconds)
  const minutes = Math.floor(whole / 60)

  if (minutes < 60) return `${minutes}m${whole % 60}s`

  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, '0')}m`
}

function count(value: number, singular: string, plural = `${singular}s`): string {
  return `${value} ${value === 1 ? singular : plural}`
}

/**
 * The line's groups, in reading order: what the run did, where its time went,
 * how fast the model answered, and what it cost. A group with nothing
 * measured drops out whole rather than printing a zero — a resumed session,
 * whose file records no first-token times, has no speed group at all.
 */
export function statsGroups(stats: TrajectoryStats): string[] {
  const groups: string[] = []

  if (stats.steps > 0) {
    groups.push(`${count(stats.turns, 'turn')} · ${count(stats.steps, 'step')}`)

    const durations: string[] = []

    if (stats.llmMs > 0) durations.push(`LLM ${formatCompactDuration(stats.llmMs)}`)
    if (stats.toolMs > 0) durations.push(`Tool call ${formatCompactDuration(stats.toolMs)}`)
    if (durations.length > 0) groups.push(durations.join(' · '))

    const speeds: string[] = []

    if (stats.ttftMs !== null) speeds.push(`TTFT avg ${formatCompactDuration(stats.ttftMs)}`)
    if (stats.throughput !== null) speeds.push(`${formatSpeed(stats.throughput)} tok/s`)
    if (speeds.length > 0) groups.push(speeds.join(' · '))
  }

  const billed = billedInput(stats)

  // Gated on actual token activity: a run whose every request failed shows
  // its counts without a zero-token group.
  if (billed > 0 || stats.outputTokens > 0) {
    if (stats.cacheHitRatio !== null) groups.push(`Cache hit ${cacheHitPercent(stats.cacheHitRatio)}%`)

    groups.push(`Input ${formatTokens(billed)} tok · Output ${formatTokens(stats.outputTokens)} tok`)
  }

  return groups
}

/**
 * The run's totals under the composer, as one line of pipe-separated groups —
 * the reference client's stats strip.
 *
 * `2 turns · 106 steps | LLM 6m28s · Tool call 23.7s | TTFT avg 1.3s · 258
 * tok/s | Cache hit 99% | Input 11.5M tok · Output 65.9K tok`. Every figure
 * is the ledger's (`trajectoryStats`), so the line and the Trajectory tab
 * never disagree. Nothing renders before the first step.
 */
export function StatsLine({ stats }: StatsLineProps) {
  const groups = statsGroups(stats)

  if (groups.length === 0) return null

  return (
    <div className={css.root} data-composer-stats title={groups.join(' | ')}>
      {groups.map((group, index) => (
        // Groups are distinct by construction, but the index is the honest key
        // for a positional list.
        // eslint-disable-next-line react/no-array-index-key
        <Fragment key={index}>
          {index > 0 && (
            <span aria-hidden className={css.sep}>
              |
            </span>
          )}
          <span>{group}</span>
        </Fragment>
      ))}
    </div>
  )
}

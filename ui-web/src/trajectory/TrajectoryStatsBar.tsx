import { formatMs, formatTokens, type TrajectoryStats } from '../state/trajectory.ts'
import css from './TrajectoryStatsBar.module.css'

export interface TrajectoryStatsBarProps {
  stats: TrajectoryStats
}

/**
 * The run's totals.
 *
 * Model time and tool time are shown apart, never summed: "41s" tells you
 * nothing actionable, while "41s of model, 22s of tools" tells you which half
 * to go look at. Figures that were never measured are omitted rather than
 * printed as zero.
 */
export function TrajectoryStatsBar({ stats }: TrajectoryStatsBarProps) {
  if (stats.turns === 0) return null

  return (
    <div className={css.root}>
      <span className={css.group}>
        <span>
          <span className={css.value}>{stats.turns}</span> {stats.turns === 1 ? 'turn' : 'turns'}
        </span>
        <span className={css.dot}>·</span>
        <span>
          <span className={css.value}>{stats.steps}</span> {stats.steps === 1 ? 'step' : 'steps'}
        </span>
      </span>

      <span className={css.group}>
        <span>
          LLM <span className={css.value}>{formatMs(stats.llmMs)}</span>
        </span>
        <span className={css.dot}>·</span>
        <span>
          Tools <span className={css.value}>{formatMs(stats.toolMs)}</span>
        </span>
      </span>

      {(stats.ttftMs !== null || stats.throughput !== null) && (
        <span className={css.group}>
          {stats.ttftMs !== null && (
            <span>
              TTFT avg <span className={css.value}>{formatMs(stats.ttftMs)}</span>
            </span>
          )}
          {stats.throughput !== null && (
            <span>
              <span className={css.value}>{stats.throughput.toFixed(0)}</span> tok/s
            </span>
          )}
        </span>
      )}

      {stats.cacheHitRatio !== null && (
        <span className={css.group}>
          <span>
            Cache hit{' '}
            <span className={css.value}>{Math.round(stats.cacheHitRatio * 100)}%</span>
          </span>
        </span>
      )}

      <span className={css.group}>
        <span>
          Input <span className={css.value}>{formatTokens(stats.inputTokens)}</span> tok
        </span>
        <span className={css.dot}>·</span>
        <span>
          Output <span className={css.value}>{formatTokens(stats.outputTokens)}</span> tok
        </span>
      </span>
    </div>
  )
}

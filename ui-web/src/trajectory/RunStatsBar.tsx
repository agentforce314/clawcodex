import { qualifiedModel } from '../conversation/ModelSelect.tsx'
import { formatMs, formatTokens, type TrajectoryStats } from '../state/trajectory.ts'
import css from './RunStatsBar.module.css'

export interface RunStatsBarProps {
  model?: string
  provider?: string
  stats: TrajectoryStats
}

/**
 * The run's totals, under the composer on both tabs.
 *
 * Cost and latency belong where people actually sit, which is the chat — not
 * one tab over. It carries the model as well, because "156 tok/s" only means
 * something once you know what produced it, and the composer chip shows the
 * model without the provider that served it.
 *
 * Model time and tool time are shown apart, never summed: "41s" tells you
 * nothing actionable, while "41s of model, 22s of tools" tells you which half
 * to go look at. Figures that were never measured are omitted rather than
 * printed as zero.
 */
export function RunStatsBar({ model, provider, stats }: RunStatsBarProps) {
  const named = model !== undefined && model !== ''

  // Before the first turn there are no figures, and a bar holding nothing but
  // separators is worse than no bar.
  if (stats.turns === 0 && !named) return null

  return (
    <div className={css.root}>
      {named && (
        <span className={css.group}>
          <span className={css.model}>{qualifiedModel(model ?? '', provider)}</span>
        </span>
      )}

      {stats.turns > 0 && (
      <span className={css.group}>
        <span>
          <span className={css.value}>{stats.turns}</span> {stats.turns === 1 ? 'turn' : 'turns'}
        </span>
        <span className={css.dot}>·</span>
        <span>
          <span className={css.value}>{stats.steps}</span> {stats.steps === 1 ? 'step' : 'steps'}
        </span>
      </span>
      )}

      {stats.turns > 0 && (
      <span className={css.group}>
        <span>
          LLM <span className={css.value}>{formatMs(stats.llmMs)}</span>
        </span>
        <span className={css.dot}>·</span>
        <span>
          Tools <span className={css.value}>{formatMs(stats.toolMs)}</span>
        </span>
      </span>
      )}

      {(stats.ttftMs !== null || stats.throughput !== null) && (
        <span className={css.group}>
          {stats.ttftMs !== null && (
            <span>
              TTFT avg <span className={css.value}>{formatMs(stats.ttftMs)}</span>
            </span>
          )}
          {stats.ttftMs !== null && stats.throughput !== null && <span className={css.dot}>·</span>}
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

      {/* A rehydrated ledger has real timings but no usage; zeros here would
          read as "this run was free", so the group waits for a measurement. */}
      {stats.turns > 0 && stats.inputTokens + stats.outputTokens > 0 && (
        <span className={css.group}>
          <span>
            Input <span className={css.value}>{formatTokens(stats.inputTokens)}</span> tok
          </span>
          <span className={css.dot}>·</span>
          <span>
            Output <span className={css.value}>{formatTokens(stats.outputTokens)}</span> tok
          </span>
        </span>
      )}
    </div>
  )
}

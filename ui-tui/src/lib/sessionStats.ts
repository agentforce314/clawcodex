/**
 * Accumulated session stats for the persistent line under the composer —
 * the deleted REPL's bottom toolbar (repl/core.py `_bottom_toolbar`):
 *
 *   deepseek · deepseek-v4-flash · ~/work/app · turns: 3 · tokens: 33189 in / 622 out · cost $0.0048
 *
 * Token/cost totals derive from the backend's end-of-turn CostSnapshot (the
 * same accumulators /cost prints, subagents included, restored on /resume);
 * the turn count is the server's `session_turns` odometer.
 */
import { stringWidth } from '@clawcodex/ink'

import { shortCwd } from '../domain/paths.js'
import type { CostSnapshot } from '../gatewayTypes.js'

import { formatCost } from './costSummary.js'

export interface SessionStats {
  costUsd: number
  /** Total prompt tokens sent: uncached input + cache reads + cache writes. */
  inputTokens: number
  outputTokens: number
  turns: number
}

export const ZERO_SESSION_STATS: SessionStats = { costUsd: 0, inputTokens: 0, outputTokens: 0, turns: 0 }

/** Fold a CostSnapshot's per-model accumulators into display totals. */
export function statsFromCostSnapshot(snap: CostSnapshot, turns: number): SessionStats {
  let input = 0
  let output = 0

  for (const u of Object.values(snap.model_usage ?? {})) {
    input += (u.input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0)
    output += u.output_tokens ?? 0
  }

  return { costUsd: snap.total_cost_usd ?? 0, inputTokens: input, outputTokens: output, turns }
}

export interface SessionStatsLineInput {
  cols: number
  cwd: string
  model: string
  provider: string
  stats: SessionStats
}

/**
 * Compose the stats line, shedding cwd detail as the terminal narrows:
 * full path → `~`-abbreviated → 28-col tail → 12-col tail → no cwd. The
 * accumulators on the right are the payload, so the path yields first; if
 * even the cwd-less form overflows, the caller's truncate-end clips it.
 */
export function buildSessionStatsLine({ cols, cwd, model, provider, stats }: SessionStatsLineInput): string {
  const tail = [
    `turns: ${stats.turns}`,
    `tokens: ${stats.inputTokens} in / ${stats.outputTokens} out`,
    ...(stats.costUsd > 0 ? [`cost ${formatCost(stats.costUsd)}`] : [])
  ]

  const home = process.env.HOME
  const tilde = home && cwd.startsWith(home) ? `~${cwd.slice(home.length)}` : cwd
  const cwdVariants = cwd ? [cwd, tilde, shortCwd(cwd, 28), shortCwd(cwd, 12), ''] : ['']

  let line = ''

  for (const variant of cwdVariants) {
    line = [provider, model, variant, ...tail].filter(Boolean).join(' · ')

    if (stringWidth(line) <= cols) {
      return line
    }
  }

  return line
}

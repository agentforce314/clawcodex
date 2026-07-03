/**
 * Session cost summary — the original's formatTotalCost block
 * (cost-tracker.ts:249-265) with its format helpers (utils/format.ts),
 * ported verbatim but fed from the backend's cost snapshot (accounting
 * lives in the Python bootstrap singleton, not in-process):
 *
 *   Total cost:            $0.0567
 *   Total duration (API):  1m 4s
 *   Total duration (wall): 12m 8s
 *   Total code changes:    10 lines added, 2 lines removed
 *   Usage by model:
 *           deepseek-chat:  1.2k input, 3.4k output, 100.5k cache read ($0.06)
 *
 * Two consumers: /cost (fresh `cost` control query) and the exit summary
 * (the original's useCostSummary process-exit hook, costHook.ts:12), which
 * prints the last end-of-turn snapshot after the TUI unmounts.
 */
import type { CostSnapshot } from '../gatewayTypes.js'

const round = (n: number, precision: number): number => Math.round(n * precision) / precision

/** Original formatCost: 2 decimals above $0.50, 4 below (cost-tracker.ts:194). */
export function formatCost(cost: number, maxDecimalPlaces = 4): string {
  return `$${cost > 0.5 ? round(cost, 100).toFixed(2) : cost.toFixed(maxDecimalPlaces)}`
}

/** Original formatDuration (utils/format.ts:34), default-options path only. */
export function formatDuration(ms: number): string {
  if (ms < 60_000) {
    if (ms === 0) {
      return '0s'
    }

    if (ms < 1) {
      return `${(ms / 1000).toFixed(1)}s`
    }

    return `${Math.floor(ms / 1000)}s`
  }

  let days = Math.floor(ms / 86_400_000)
  let hours = Math.floor((ms % 86_400_000) / 3_600_000)
  let minutes = Math.floor((ms % 3_600_000) / 60_000)
  let seconds = Math.round((ms % 60_000) / 1000)

  // Rounding carry-over (59.5s → 60s).
  if (seconds === 60) {
    seconds = 0
    minutes++
  }

  if (minutes === 60) {
    minutes = 0
    hours++
  }

  if (hours === 24) {
    hours = 0
    days++
  }

  if (days > 0) {
    return `${days}d ${hours}h ${minutes}m`
  }

  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`
  }

  if (minutes > 0) {
    return `${minutes}m ${seconds}s`
  }

  return `${seconds}s`
}

// Original formatNumber (utils/format.ts:124): Intl compact notation,
// lowercased, with consistent decimals only from 1000 up ("900", "1.3k").
let fmtConsistent: Intl.NumberFormat | null = null
let fmtLoose: Intl.NumberFormat | null = null

export function formatNumber(n: number): string {
  if (n >= 1000) {
    fmtConsistent ??= new Intl.NumberFormat('en-US', {
      maximumFractionDigits: 1,
      minimumFractionDigits: 1,
      notation: 'compact'
    })

    return fmtConsistent.format(n).toLowerCase()
  }

  fmtLoose ??= new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 1,
    minimumFractionDigits: 0,
    notation: 'compact'
  })

  return fmtLoose.format(n).toLowerCase()
}

/** Original formatModelUsage (cost-tracker.ts:198). Model names are used
 *  as-is — the backend reports provider model ids, already canonical. */
function formatModelUsage(modelUsage: CostSnapshot['model_usage']): string {
  const entries = Object.entries(modelUsage ?? {})

  if (entries.length === 0) {
    return 'Usage:                 0 input, 0 output'
  }

  let result = 'Usage by model:'

  for (const [model, u] of entries) {
    let usage = `  ${formatNumber(u.input_tokens ?? 0)} input, ${formatNumber(u.output_tokens ?? 0)} output`

    if ((u.cache_read_input_tokens ?? 0) > 0) {
      usage += `, ${formatNumber(u.cache_read_input_tokens!)} cache read`
    }

    if ((u.cache_creation_input_tokens ?? 0) > 0) {
      usage += `, ${formatNumber(u.cache_creation_input_tokens!)} cache write`
    }

    if ((u.web_search_requests ?? 0) > 0) {
      usage += `, ${formatNumber(u.web_search_requests!)} web search`
    }

    usage += ` (${formatCost(u.cost_usd ?? 0)})`
    result += `\n${`${model}:`.padStart(21)}${usage}`
  }

  return result
}

/** Original formatTotalCost (cost-tracker.ts:249), unstyled — the caller
 *  dims it (transcript sys line / ANSI dim on the exit print). */
export function formatTotalCost(s: CostSnapshot): string {
  const added = s.total_lines_added ?? 0
  const removed = s.total_lines_removed ?? 0

  const costDisplay =
    formatCost(s.total_cost_usd ?? 0) +
    (s.has_unknown_model_cost ? ' (costs may be inaccurate due to usage of unknown models)' : '')

  return (
    `Total cost:            ${costDisplay}\n` +
    `Total duration (API):  ${formatDuration(s.total_api_duration_ms ?? 0)}\n` +
    `Total duration (wall): ${formatDuration(s.total_duration_ms ?? 0)}\n` +
    `Total code changes:    ${added} ${added === 1 ? 'line' : 'lines'} added, ` +
    `${removed} ${removed === 1 ? 'line' : 'lines'} removed\n` +
    formatModelUsage(s.model_usage)
  )
}

// ── exit summary ────────────────────────────────────────────────────────────
// The backend refreshes the snapshot on every end-of-turn result message;
// the exit hook prints the last one synchronously (process.on('exit') runs
// sync code only, and TTY stdout writes are synchronous in Node).

let lastSnapshot: CostSnapshot | null = null
let lastSnapshotAt = 0

export function setLastCostSnapshot(s: CostSnapshot | null | undefined): void {
  if (s && Object.keys(s).length > 0) {
    lastSnapshot = s
    lastSnapshotAt = Date.now()
  }
}

export function getLastCostSnapshot(): CostSnapshot | null {
  return lastSnapshot
}

/**
 * The original prints formatTotalCost on process exit (costHook.ts:12,
 * gated on console-billing access — clawcodex talks to API-key providers,
 * so it always applies). Deliberate divergence: with zero completed turns
 * there is no snapshot and nothing prints (the original would print a
 * zeroed block). Register AFTER entry.tsx's terminal-mode reset backstop
 * so the block lands on a sane terminal, under the final frame.
 */
export function registerCostSummaryOnExit(): void {
  process.on('exit', () => {
    if (!lastSnapshot) {
      return
    }

    // The original reads wall duration AT exit (getTotalDuration()); the
    // snapshot's is frozen at the last turn's end, so extend it by the idle
    // time since. API duration/cost/lines are legitimately turn-frozen.
    const s: CostSnapshot = {
      ...lastSnapshot,
      total_duration_ms: (lastSnapshot.total_duration_ms ?? 0) + Math.max(0, Date.now() - lastSnapshotAt)
    }

    process.stdout.write(`\n\x1b[2m${formatTotalCost(s)}\x1b[22m\n`)
  })
}

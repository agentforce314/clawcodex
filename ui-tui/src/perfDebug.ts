/**
 * Opt-in performance diagnostics (CLAWCODEX_DEBUG_PERF=1).
 *
 * Logs event-loop stalls — periods where the Node event loop was blocked longer
 * than a threshold — to ~/.clawcodex/perf.log, tagged with the last "activity"
 * breadcrumb (a render, a backend message, or a keypress). When input feels
 * stuck, the log pinpoints WHERE the block is: JS render/processing (a stall
 * appears) vs. terminal/backend (no stall appears). No-ops unless enabled.
 */
import { appendFileSync, mkdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const PERF_DEBUG = process.env['CLAWCODEX_DEBUG_PERF'] === '1'
const LOG_PATH = join(homedir(), '.clawcodex', 'perf.log')

let lastActivity = 'startup'
let activityAt = Date.now()
let renderCount = 0

function write(line: string): void {
  try {
    mkdirSync(join(homedir(), '.clawcodex'), { recursive: true })
    appendFileSync(LOG_PATH, line + '\n')
  } catch {
    /* best effort */
  }
}

/** Record what the app is doing right now (cheap; only when enabled). */
export function note(activity: string): void {
  if (!PERF_DEBUG) return
  lastActivity = activity
  activityAt = Date.now()
}

/** Count a React render (helps tell render-storms from single blocks). */
export function bumpRender(): void {
  if (PERF_DEBUG) renderCount++
}

/** Start the stall detector. Call once at startup. */
export function startStallDetector(): void {
  if (!PERF_DEBUG) return
  write(`\n=== clawcodex perf session ${new Date().toISOString()} (log: ${LOG_PATH}) ===`)
  const INTERVAL = 100
  const THRESHOLD = 300 // report blocks longer than this
  let last = Date.now()
  const timer = setInterval(() => {
    const now = Date.now()
    const gap = now - last - INTERVAL
    if (gap > THRESHOLD) {
      write(
        `${new Date().toISOString()} STALL ${gap}ms · lastActivity="${lastActivity}" (${now - activityAt}ms ago) · renders=${renderCount}`,
      )
    }
    last = now
  }, INTERVAL)
  timer.unref?.()
}

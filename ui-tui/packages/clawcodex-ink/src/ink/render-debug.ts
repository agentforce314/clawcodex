/**
 * Opt-in render tracing for the "stray characters on screen" class of bug.
 *
 * The renderer owns the screen through an incremental cell diff, so anything
 * that makes its model disagree with the real terminal shows up as glyphs it
 * can never clean up. It has TWO places where it knowingly does not paint a
 * cell, and they fail in opposite directions:
 *
 *   1. The scrolled-off guard in log-update's diff pass drops changes to rows
 *      it believes are already in terminal scrollback. If that belief is ever
 *      wrong the row keeps its OLD content — a stray glyph.
 *   2. writeCellWithStyleStr refuses a wide char that would cross the viewport
 *      edge. That one is worse: the model still records the cell as painted, so
 *      prev === next on the following frame and nothing ever repaints it —
 *      permanent divergence at the right edge, showing as a MISSING glyph.
 *
 * Set CLAWCODEX_RENDER_DEBUG=1 to record both. Output goes to a FILE, never
 * stdout — writing a diagnostic to the terminal being diagnosed would be its
 * own foreign write. Defaults to clawcodex-render-debug.log in the system temp
 * directory (not the cwd, which would drop it into the user's repo mid-debug);
 * override with CLAWCODEX_RENDER_DEBUG_FILE.
 */

import { appendFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'

const enabled = (): boolean => {
  const v = process.env.CLAWCODEX_RENDER_DEBUG

  return !!v && v !== '0' && v !== 'false'
}

const target = (): string => process.env.CLAWCODEX_RENDER_DEBUG_FILE || join(tmpdir(), 'clawcodex-render-debug.log')

/** True when tracing is on. Read ONCE per frame — this is a process.env lookup
 *  (~190ns), far too slow for the per-cell diff path. */
export const renderDebugEnabled = enabled

function record(entry: Record<string, number | string>): void {
  try {
    appendFileSync(target(), `${JSON.stringify({ ...entry, at: new Date().toISOString() })}\n`)
  } catch {
    // Diagnostics must never take the UI down — a read-only cwd or a full disk
    // is not a rendering problem.
  }
}

/**
 * Record a diff change dropped because its row was judged unreachable.
 *
 * Call once per ROW, after the diff pass — never per cell. The condition worth
 * catching is a burst (a virtualization slide drops thousands of cells in one
 * frame), and a blocking write per cell would stall the render loop whose
 * timing is under investigation.
 *
 * `viewportY` is where the renderer thinks the visible band starts and
 * `physCursorRow` is the tracked physical row it derives that from; when a drop
 * coincides with visible corruption, those two are the suspects.
 */
export function logDroppedRow(info: {
  nextLine: string
  physCursorRow: number
  prevLine: string
  screenHeight: number
  viewportHeight: number
  viewportY: number
  y: number
}): void {
  if (!enabled()) {
    return
  }

  record({ ...info, event: 'dropped-scrolled-off-row' })
}

/**
 * Record a wide char refused at the viewport edge (drop site 2 above). Rare by
 * construction — at most a couple per frame — so this one is safe per cell.
 */
export function logSkippedWideCell(info: { char: string; viewportWidth: number; x: number; y: number }): void {
  if (!enabled()) {
    return
  }

  record({ ...info, event: 'skipped-wide-cell-at-edge' })
}

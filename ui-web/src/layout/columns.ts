/**
 * Pure concession-chain solver for the three-column shell.
 *
 * Chain order is fixed by contract: hold the centre column at CENTER_MIN by
 * shrinking details, then by auto-closing it. The sidebar never concedes — its
 * rendered width is always the drag preference (or the collapsed rail), and
 * the centre absorbs any remaining deficit as the last resort.
 *
 * No hysteresis: the output is a function of (viewport, preferences) only, so
 * re-widening the window restores the previous layout automatically. Auto-close
 * is *derived* — the stored preferences are never rewritten, which is what
 * makes the recovery lossless.
 *
 * Adapted from the DeepSeek Harness web client (MIT; see ui-web/README.md).
 */

export interface Columns {
  center: number
  details: number
  sidebar: number
}

/** Centre column floor; only the final fallback may go below it. */
export const CENTER_MIN = 640
/** Sidebar drag clamp range and resting width. */
export const SIDEBAR_MIN = 264
export const SIDEBAR_MAX = 420
export const SIDEBAR_DEFAULT = 280
/** Closed-sidebar rail: a 24px icon column between 16px horizontal paddings. */
export const SIDEBAR_COLLAPSED = 56
/**
 * Viewport width below which the sidebar auto-collapses to the rail. A manual
 * toggle below it re-expands over the squeezed centre, so the breakpoint is
 * consumed by the frame (which decides the effective preference) and never by
 * this solver.
 */
export const SIDEBAR_AUTO_COLLAPSE = 1024
/** Details drag clamp range and resting width. */
export const DETAILS_MIN = 300
export const DETAILS_MAX = 520
export const DETAILS_DEFAULT = 360

export function clampWidth(px: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(px)))
}

/**
 * Solve the three column widths for one viewport frame.
 *
 * @param viewport - available frame width in px.
 * @param sidebar - sidebar width preference (0 = closed → the compact rail).
 * @param details - details width preference (0 = closed → zero width; the
 *   subtree stays mounted, so its state survives a close).
 */
export function computeColumns(viewport: number, sidebar: number, details: number): Columns {
  const s = sidebar === 0 ? SIDEBAR_COLLAPSED : clampWidth(sidebar, SIDEBAR_MIN, SIDEBAR_MAX)
  const d0 = details === 0 ? 0 : clampWidth(details, DETAILS_MIN, DETAILS_MAX)

  // 1. Everything fits at preferred widths.
  if (s + d0 + CENTER_MIN <= viewport) {
    return { center: viewport - s - d0, details: d0, sidebar: s }
  }

  // 2. Shrink details toward its minimum.
  const d1 = d0 === 0 ? 0 : Math.max(DETAILS_MIN, viewport - s - CENTER_MIN)

  if (s + d1 + CENTER_MIN <= viewport) return { center: CENTER_MIN, details: d1, sidebar: s }

  // 3. Auto-close details; the centre absorbs whatever deficit is left.
  return { center: Math.max(0, viewport - s), details: 0, sidebar: s }
}

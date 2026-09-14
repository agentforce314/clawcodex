/**
 * Pure concession-chain solver for the three-column shell.
 *
 * Chain order is fixed by contract: hold the centre column at CENTER_MIN by
 * shrinking details, then by dropping its track. The sidebar never concedes —
 * its rendered width is always the drag preference (or the collapsed rail),
 * and the centre absorbs any remaining deficit as the last resort.
 *
 * The right column has no fixed ceiling: it may take up to DETAILS_MAX_RATIO
 * of the frame, and past that only the centre's floor holds it back — so a
 * wide window reads a file beside the conversation at whatever width the
 * reader drags, as the reference's right column does.
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

/** Centre width protected while the right column is open; without it the centre may fall to zero. */
export const CENTER_MIN = 400
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
/** Right column drag clamp floor. */
export const DETAILS_MIN = 300
/** The most of the frame the right column may take when dragged. */
export const DETAILS_MAX_RATIO = 0.7
/** The share of the frame the right column opens at the first time. */
export const DETAILS_DEFAULT_RATIO = 0.45

/** The right column's drag ceiling for one frame width, never below its floor. */
export function detailsMax(viewport: number): number {
  return Math.max(DETAILS_MIN, Math.round(viewport * DETAILS_MAX_RATIO))
}

/** The width the right column opens at the first time, for one frame width. */
export function detailsDefault(viewport: number): number {
  return clampWidth(viewport * DETAILS_DEFAULT_RATIO, DETAILS_MIN, detailsMax(viewport))
}

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
  // What the right column may have once the sidebar and the centre floor are
  // paid for. Under its own floor it drops its track rather than squeeze.
  const available = viewport - s - CENTER_MIN
  const d =
    details === 0 || available < DETAILS_MIN
      ? 0
      : Math.min(available, clampWidth(details, DETAILS_MIN, detailsMax(viewport)))

  return { center: Math.max(0, viewport - s - d), details: d, sidebar: s }
}

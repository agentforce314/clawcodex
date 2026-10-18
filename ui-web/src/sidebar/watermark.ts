import type { SessionRow } from '../gateway/protocol.ts'

export const SESSION_PAGE = 5

/** Keep a contiguous prefix, revealing attention rows within the first 50 without shrinking it again. */
export function sessionWatermark(rows: readonly SessionRow[], previous: number, active: string | null): number {
  let needed = SESSION_PAGE
  rows.slice(0, 50).forEach((row, index) => {
    if (row.id === active || row.is_active) needed = index + 1
  })
  return Math.max(previous, needed)
}

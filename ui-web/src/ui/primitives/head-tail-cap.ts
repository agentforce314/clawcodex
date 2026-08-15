/**
 * Shared truncation arithmetic for the card primitives (terminal, diff, read).
 *
 * A capped card shows the HEAD and the TAIL with the fold in the middle,
 * because the tail is where a command's verdict lives — the exit error, the
 * failing test name, the "N files changed" line. Head-only truncation hides
 * exactly the lines the reader opened the card for.
 *
 * The split mirrors the DeepSeek Harness card rule: head = ⌈max/2⌉, tail =
 * the rest, and a cap that saves less than `SLACK` lines is not applied at
 * all — a "… 2 more lines" button costs more attention than the lines do.
 */

export interface HeadTailCap {
  /** Lines hidden between the two windows; 0 means the cap did not engage. */
  hidden: number
  /** Line count of the leading window. */
  head: number
  /** Line count of the trailing window. */
  tail: number
}

const SLACK = 4

export function headTailCap(total: number, maxLines: number): HeadTailCap {
  if (total <= maxLines + SLACK) {
    return { head: total, hidden: 0, tail: 0 }
  }

  const head = Math.ceil(maxLines / 2)
  const tail = maxLines - head

  return { head, hidden: total - maxLines, tail }
}

/** Apply a cap to a line array: `[head, hidden, tail]` windows. */
export function splitByCap<T>(lines: T[], cap: HeadTailCap): { head: T[]; tail: T[] } {
  if (cap.hidden === 0) return { head: lines, tail: [] }

  return { head: lines.slice(0, cap.head), tail: lines.slice(lines.length - cap.tail) }
}

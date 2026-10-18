import type { MenuRow } from './command-menu.ts'

/** Recognized argument-taking commands occupy an atomic chip ahead of the editable argument. */
export function commandDraft(text: string, rows: readonly MenuRow[]): { row: MenuRow; prefix: string; body: string } | null {
  const match = /^(\/[^\s]+) /.exec(text)
  if (!match) return null
  const row = rows.find(row => row.name === match[1] && row.hint !== undefined && row.action === undefined)
  return row ? { row, prefix: match[0], body: text.slice(match[0].length) } : null
}

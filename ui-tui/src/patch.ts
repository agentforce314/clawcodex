/**
 * Build display patches (StructuredPatchHunk[]) from Edit/Write/MultiEdit tool
 * inputs — ported from openclaude's typescript/src/utils/diff.ts so our hunks
 * match the original byte-for-byte before they reach the ColorDiff renderer.
 *
 * Dropped vs upstream: the analytics/cost-tracker side effects (logEvent,
 * addToTotalLinesChanged) — the agent backend owns those; the client only
 * renders.
 */
import { structuredPatch, type StructuredPatchHunk } from 'diff'

export const CONTEXT_LINES = 3
export const DIFF_TIMEOUT_MS = 5_000

/** Leading tabs → 2 spaces each (display only), matching upstream. */
export function convertLeadingTabsToSpaces(content: string): string {
  if (!content.includes('\t')) return content
  return content.replace(/^\t+/gm, m => '  '.repeat(m.length))
}

// `&` and `$` confuse the diff library's replace step; swap for tokens, then
// restore after the patch is computed.
const AMPERSAND_TOKEN = '<<:AMPERSAND_TOKEN:>>'
const DOLLAR_TOKEN = '<<:DOLLAR_TOKEN:>>'

function escapeForDiff(s: string): string {
  return s.replaceAll('&', AMPERSAND_TOKEN).replaceAll('$', DOLLAR_TOKEN)
}

function unescapeFromDiff(s: string): string {
  return s.replaceAll(AMPERSAND_TOKEN, '&').replaceAll(DOLLAR_TOKEN, '$')
}

export interface FileEdit {
  old_string: string
  new_string: string
  replace_all?: boolean
}

/** Diff two whole contents (used for Write / new files). */
export function getPatchFromContents({
  filePath,
  oldContent,
  newContent,
  ignoreWhitespace = false,
  singleHunk = false,
}: {
  filePath: string
  oldContent: string
  newContent: string
  ignoreWhitespace?: boolean
  singleHunk?: boolean
}): StructuredPatchHunk[] {
  const result = structuredPatch(
    filePath,
    filePath,
    escapeForDiff(oldContent),
    escapeForDiff(newContent),
    undefined,
    undefined,
    {
      ignoreWhitespace,
      context: singleHunk ? 100_000 : CONTEXT_LINES,
      timeout: DIFF_TIMEOUT_MS,
    },
  )
  if (!result) return []
  return result.hunks.map(h => ({
    ...h,
    lines: h.lines.map(unescapeFromDiff),
  }))
}

/**
 * Patch for display with edits applied to `fileContents`. Leading tabs are
 * rendered as spaces. Mirrors upstream getPatchForDisplay exactly.
 */
export function getPatchForDisplay({
  filePath,
  fileContents,
  edits,
  ignoreWhitespace = false,
}: {
  filePath: string
  fileContents: string
  edits: FileEdit[]
  ignoreWhitespace?: boolean
}): StructuredPatchHunk[] {
  const preparedFileContents = escapeForDiff(
    convertLeadingTabsToSpaces(fileContents),
  )
  const result = structuredPatch(
    filePath,
    filePath,
    preparedFileContents,
    edits.reduce((p, edit) => {
      const { old_string, new_string } = edit
      const replace_all = 'replace_all' in edit ? edit.replace_all : false
      const escapedOldString = escapeForDiff(
        convertLeadingTabsToSpaces(old_string),
      )
      const escapedNewString = escapeForDiff(
        convertLeadingTabsToSpaces(new_string),
      )
      if (replace_all) {
        return p.replaceAll(escapedOldString, () => escapedNewString)
      }
      return p.replace(escapedOldString, () => escapedNewString)
    }, preparedFileContents),
    undefined,
    undefined,
    {
      context: CONTEXT_LINES,
      ignoreWhitespace,
      timeout: DIFF_TIMEOUT_MS,
    },
  )
  if (!result) return []
  return result.hunks.map(h => ({
    ...h,
    lines: h.lines.map(unescapeFromDiff),
  }))
}

/** Count +/- lines across a patch (for the "N additions, M removals" header). */
export function countPatchLines(patch: StructuredPatchHunk[]): {
  added: number
  removed: number
} {
  let added = 0
  let removed = 0
  for (const hunk of patch) {
    for (const line of hunk.lines) {
      if (line.startsWith('+')) added++
      else if (line.startsWith('-')) removed++
    }
  }
  return { added, removed }
}

export type { StructuredPatchHunk }

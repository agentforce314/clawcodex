/**
 * Renders an Edit/MultiEdit/Write tool result the way the original Claude Code
 * does, driving openclaude's pure-TS ColorDiff / ColorFile renderers (see
 * ../colorDiff.ts). Layout mirrors MessageResponse + FileEditToolUpdatedMessage /
 * FileWriteToolCreatedMessage:
 *
 *   ⎿  Added N lines, removed M lines        (edit summary; omitted for writes)
 *      <diff hunks | highlighted new content, width = columns - 12>
 *      … +N lines                            (write truncation hint)
 *
 * ColorDiff/ColorFile emit fully ANSI-escaped rows (dimmed line-number gutter,
 * +/-/space markers, add/remove backgrounds, highlight.js syntax colors,
 * word-level diff), so we just print each row in a <Text>. No dashed frame —
 * that belongs to the permission preview, not the result message.
 */
import { Box, Text } from 'ink'
import React from 'react'
import { ColorDiff, ColorFile } from '../colorDiff.js'
import { countPatchLines } from '../patch.js'
import type { ToolDiff } from '../diff.js'
import { theme } from '../theme.js'

// CC's default dark theme — Monokai syntax colors + dark add/del tints.
const THEME_NAME = 'dark'
// Original truncates created-file previews to the first 10 lines.
const WRITE_MAX_LINES = 10
// Safety cap so a giant edit can't flood the live viewport.
const EDIT_MAX_LINES = 240

/** "Added 3 lines, removed 2 lines" — exact phrasing/casing from upstream. */
function editSummary(added: number, removed: number): string {
  const parts: string[] = []
  if (added > 0) parts.push(`Added ${added} ${added > 1 ? 'lines' : 'line'}`)
  if (removed > 0) {
    const verb = added === 0 ? 'Removed' : 'removed'
    parts.push(`${verb} ${removed} ${removed > 1 ? 'lines' : 'line'}`)
  }
  return parts.join(', ')
}

export function DiffView({ diff }: { diff: ToolDiff }): React.ReactElement | null {
  const cols = process.stdout.columns ?? 80
  // Exact width the original passes to its diff/code renderers.
  const width = Math.max(20, cols - 12)

  const bodyRows: { text: string; sep?: boolean }[] = []
  let summary = ''
  let hint = ''

  if (diff.kind === 'write') {
    const content = diff.content ?? ''
    const all = content.split('\n')
    const shown = all.slice(0, WRITE_MAX_LINES).join('\n')
    const lines = new ColorFile(shown, diff.filePath).render(THEME_NAME, width, false) ?? []
    for (const l of lines) bodyRows.push({ text: l })
    const extra = all.length - WRITE_MAX_LINES
    if (extra > 0) hint = `… +${extra} ${extra === 1 ? 'line' : 'lines'}`
  } else {
    if (!diff.hunks.length) return null
    const { added, removed } = countPatchLines(diff.hunks)
    summary = editSummary(added, removed)
    diff.hunks.forEach((hunk, i) => {
      if (i > 0) bodyRows.push({ text: '...', sep: true })
      const lines =
        new ColorDiff(hunk, diff.firstLine, diff.filePath, diff.fileContent ?? null).render(
          THEME_NAME,
          width,
          false,
        ) ?? []
      for (const l of lines) bodyRows.push({ text: l })
    })
    const over = bodyRows.length - EDIT_MAX_LINES
    if (over > 0) {
      bodyRows.length = EDIT_MAX_LINES
      hint = `… +${over} ${over === 1 ? 'line' : 'lines'}`
    }
  }

  return (
    <Box flexDirection="row">
      <Box flexShrink={0}>
        <Text color={theme.dim}>{'  ⎿  '}</Text>
      </Box>
      <Box flexDirection="column" flexGrow={1}>
        {summary ? <Text>{summary}</Text> : null}
        {bodyRows.map((r, i) =>
          r.sep ? (
            <Text key={i} dimColor>
              {'...'}
            </Text>
          ) : (
            <Text key={i}>{r.text}</Text>
          ),
        )}
        {hint ? <Text color={theme.dim}>{hint}</Text> : null}
      </Box>
    </Box>
  )
}

/**
 * Renders a line diff (from diff.ts) the way the original StructuredDiff does:
 * a NON-colored line-number gutter column + a colored content column (green
 * added / red removed / dim context). Long lines WRAP — each wrapped row keeps
 * the marker and the background color, and the line number shows only on the
 * first row — instead of being truncated off the right edge. Topped with an
 * "⎿ Added N, removed M lines" summary.
 *
 * Line numbers are relative to the edited region: the tool input gives us
 * old_string/new_string, not absolute file positions.
 */
import { Box, Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'
import type { DiffLine } from '../diff.js'

const MAX_LINES = 80

/** Hard-wrap into <=width segments (code wraps mid-token, like the original). */
function wrapSegs(s: string, width: number): string[] {
  if (width < 1 || s.length <= width) return [s || ' ']
  const out: string[] = []
  for (let i = 0; i < s.length; i += width) out.push(s.slice(i, i + width))
  return out
}

export function DiffView({ lines }: { lines: DiffLine[] }): React.ReactElement {
  const shown = lines.slice(0, MAX_LINES)
  const extra = lines.length - shown.length
  const numW = Math.max(1, ...lines.map((l) => String(l.oldNo ?? l.newNo ?? 0).length))
  const cols = process.stdout.columns ?? 80
  const gutterW = numW + 1
  const contentW = Math.max(8, cols - gutterW - 1) // content column fills the rest of the row
  const segW = Math.max(4, contentW - 2) // room for "<marker> "
  const added = lines.filter((l) => l.type === 'add').length
  const removed = lines.filter((l) => l.type === 'del').length

  return (
    <Box flexDirection="column">
      <Text color={theme.dim}>
        {`  ⎿ Added ${added} line${added === 1 ? '' : 's'}, removed ${removed} line${removed === 1 ? '' : 's'}`}
      </Text>
      {shown.map((l, i) => {
        const no = l.type === 'del' ? l.oldNo : l.newNo
        const marker = l.type === 'add' ? '+' : l.type === 'del' ? '-' : ' '
        const bg = l.type === 'add' ? theme.diffAddBg : l.type === 'del' ? theme.diffDelBg : undefined
        const fg = l.type === 'ctx' ? theme.dim : undefined
        const segs = wrapSegs(l.text, segW)
        return (
          <Box key={i}>
            <Box width={gutterW} flexShrink={0}>
              <Text color={theme.dim}>{String(no ?? '').padStart(numW)}</Text>
            </Box>
            <Box flexDirection="column">
              {segs.map((seg, j) => (
                <Text key={j} backgroundColor={bg} color={fg}>
                  {`${marker} ${seg}`.padEnd(contentW)}
                </Text>
              ))}
            </Box>
          </Box>
        )
      })}
      {extra > 0 ? <Text color={theme.dim}>{`    … +${extra} more lines`}</Text> : null}
    </Box>
  )
}

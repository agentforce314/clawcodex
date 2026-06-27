/**
 * Renders a line diff (from diff.ts) Claude-Code style: a line-number gutter +
 * marker, added lines on a green background, removed lines on a red background,
 * context lines dim. Mirrors StructuredDiff's gutter (marker + line number).
 */
import { Box, Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'
import type { DiffLine } from '../diff.js'

const MAX_LINES = 40

export function DiffView({ lines }: { lines: DiffLine[] }): React.ReactElement {
  const shown = lines.slice(0, MAX_LINES)
  const extra = lines.length - shown.length
  const width = Math.max(1, ...lines.map((l) => String(l.oldNo ?? l.newNo ?? 0).length))
  return (
    <Box flexDirection="column" marginLeft={2}>
      {shown.map((l, i) => {
        const no = l.type === 'del' ? l.oldNo : l.newNo
        const marker = l.type === 'add' ? '+' : l.type === 'del' ? '-' : ' '
        const bg = l.type === 'add' ? theme.diffAddBg : l.type === 'del' ? theme.diffDelBg : undefined
        return (
          <Text key={i} backgroundColor={bg} color={l.type === 'ctx' ? theme.dim : undefined} wrap="truncate-end">
            {` ${marker} ${String(no ?? '').padStart(width)} ${l.text || ' '} `}
          </Text>
        )
      })}
      {extra > 0 ? <Text color={theme.dim}>{`    … +${extra} more line${extra === 1 ? '' : 's'}`}</Text> : null}
    </Box>
  )
}

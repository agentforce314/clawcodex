/**
 * Live tool-progress block — the in-place "● Reading N files… └ current" that
 * updates during a turn as repeated Read-like calls stream in. Mirrors the
 * original Claude Code: repeated tools collapse into one summary whose count
 * grows, showing only the current target; it freezes into the transcript when
 * the round ends (App commits a collapsed summary).
 */
import { Box, Text } from '../ink.js'
import React from 'react'
import { theme } from '../theme.js'
import { toolActivityLabel } from '../toolMeta.js'

export interface LiveGroup {
  name: string
  count: number
  current: string
}

export function LiveTools({ groups }: { groups: LiveGroup[] }): React.ReactElement | null {
  if (!groups.length) return null
  return (
    <Box flexDirection="column">
      {groups.map((g, i) => (
        <Box key={i} flexDirection="column">
          <Text>
            <Text color={theme.accent}>⏺ </Text>
            <Text>{toolActivityLabel(g.name, g.current, g.count)}</Text>
            <Text color={theme.dim}>…</Text>
          </Text>
          {g.count > 1 ? (
            <Text color={theme.dim}>{`  └ ${(g.current || '').split(/[\\/]/).pop() || g.current}`}</Text>
          ) : null}
        </Box>
      ))}
    </Box>
  )
}

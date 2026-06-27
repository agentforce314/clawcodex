/** Slash-command autocomplete dropdown shown above the input. */
import { Box, Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'
import type { SlashCommand } from '../slashCommands.js'

interface Props {
  matches: SlashCommand[]
  selected: number
}

export function SlashMenu({ matches, selected }: Props): React.ReactElement | null {
  if (matches.length === 0) return null
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.border} paddingX={1}>
      {matches.map((c, i) => {
        const on = i === selected
        return (
          <Box key={c.name}>
            <Text color={on ? theme.accent : theme.dim} bold={on}>
              {on ? '❯ ' : '  '}
              {c.name.padEnd(10)}
            </Text>
            <Text color={theme.dim}>{c.description}</Text>
          </Box>
        )
      })}
      <Text color={theme.dim}>{'  ↑↓ select · tab complete · enter run'}</Text>
    </Box>
  )
}

/**
 * Slash-command suggestions shown above the input — matches the original
 * PromptInputFooterSuggestions: a borderless list where the selected row is a
 * full-width highlighted bar (background + inverse text), format
 * "/name   description".
 */
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
  const nameW = Math.max(...matches.map((c) => c.name.length))
  return (
    <Box flexDirection="column" marginBottom={1}>
      {matches.map((c, i) => {
        const on = i === selected
        if (on) {
          // Selected: full-width highlighted bar, › prefix, dark bold text.
          return (
            <Box key={c.name} width="100%">
              <Text backgroundColor={theme.suggestion} color="black" bold wrap="truncate">
                {`› ${c.name.padEnd(nameW)}   ${c.description} `}
              </Text>
            </Box>
          )
        }
        return (
          <Box key={c.name}>
            <Text>{`  ${c.name.padEnd(nameW)}`}</Text>
            <Text color={theme.dim}>{`   ${c.description}`}</Text>
          </Box>
        )
      })}
    </Box>
  )
}

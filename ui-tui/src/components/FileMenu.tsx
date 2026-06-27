/**
 * `@`-mention file suggestions shown above the input — mirrors SlashMenu and the
 * original's @-typeahead: a borderless list, the selected row a full-width
 * highlighted bar. The basename is emphasized; the directory is dimmed.
 */
import { Box, Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'

interface Props {
  matches: string[]
  selected: number
}

function split(path: string): { dir: string; base: string } {
  const i = path.lastIndexOf('/')
  return i >= 0 ? { dir: path.slice(0, i + 1), base: path.slice(i + 1) } : { dir: '', base: path }
}

export function FileMenu({ matches, selected }: Props): React.ReactElement | null {
  if (matches.length === 0) return null
  return (
    <Box flexDirection="column" marginBottom={1}>
      {matches.map((path, i) => {
        const { dir, base } = split(path)
        if (i === selected) {
          return (
            <Box key={path} width="100%">
              <Text backgroundColor={theme.suggestion} color="black" bold wrap="truncate">
                {`› ${path} `}
              </Text>
            </Box>
          )
        }
        return (
          <Box key={path}>
            <Text color={theme.dim}>{'  '}</Text>
            <Text color={theme.dim}>{dir}</Text>
            <Text>{base}</Text>
          </Box>
        )
      })}
    </Box>
  )
}

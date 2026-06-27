/** Bottom status line: connection dot · server · model · mode, and hints. */
import { Box, Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'

interface Props {
  connected: boolean
  serverLabel: string
  model: string
  mode: string
  busy: boolean
}

export function StatusBar({ connected, serverLabel, model, mode, busy }: Props): React.ReactElement {
  return (
    <Box marginTop={1} justifyContent="space-between">
      <Box>
        <Text color={connected ? theme.success : theme.error}>{connected ? '●' : '○'}</Text>
        <Text color={theme.dim}>{` ${serverLabel} · ${model} · ${mode}`}</Text>
      </Box>
      <Text color={theme.dim}>{`${busy ? 'working' : 'ready'} · / commands · ^C quit`}</Text>
    </Box>
  )
}

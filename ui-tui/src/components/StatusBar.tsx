/**
 * Bottom footer — matches the original PromptInputFooter: left side shows a
 * hint ("? for shortcuts"), right side shows status (a connection dot + model ·
 * mode, with the mode colored when it's non-default, like getModeColor).
 */
import { Box, Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'

interface Props {
  connected: boolean
  model: string
  mode: string
  busy: boolean
}

const MODE_COLOR: Record<string, string | undefined> = {
  default: undefined,
  acceptEdits: theme.success,
  bypassPermissions: theme.error,
  plan: 'rgb(72,150,140)', // sage (planMode)
}

export function StatusBar({ connected, model, mode, busy }: Props): React.ReactElement {
  const dot = !connected ? theme.dim : busy ? theme.warn : theme.success
  const modeColor = MODE_COLOR[mode] ?? theme.dim
  return (
    <Box marginTop={1} justifyContent="space-between">
      <Text color={theme.dim}>? for shortcuts</Text>
      <Box>
        <Text color={dot}>{'● '}</Text>
        <Text color={theme.dim}>{model}</Text>
        <Text color={theme.dim}>{' · '}</Text>
        <Text color={modeColor}>{mode}</Text>
      </Box>
    </Box>
  )
}

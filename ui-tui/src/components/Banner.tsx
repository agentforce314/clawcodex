/**
 * Welcome / session panel shown before the first message — the clawcodex
 * analogue of Claude Code's intro banner + provider box. Carries the
 * connection info (model · mode · tools · cwd) so it doesn't clutter the
 * transcript, plus a one-line tip.
 */
import { Box, Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'

interface Props {
  model: string
  mode: string
  tools: number
  cwd?: string
}

function shorten(cwd?: string): string | undefined {
  if (!cwd) return undefined
  const home = process.env['HOME']
  return home && cwd.startsWith(home) ? `~${cwd.slice(home.length)}` : cwd
}

function Row({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <Text>
      <Text color={theme.dim}>{label.padEnd(7)}</Text>
      {value}
    </Text>
  )
}

export function Banner({ model, mode, tools, cwd }: Props): React.ReactElement {
  const dir = shorten(cwd)
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.accent}
      paddingX={2}
      paddingY={1}
      marginBottom={1}
    >
      <Text>
        <Text bold color={theme.accent}>
          clawcodex
        </Text>
        <Text color={theme.dim}>{'  ·  a Claude-Code-style TUI on the Python backend'}</Text>
      </Text>
      <Box marginTop={1} flexDirection="column">
        <Row label="model" value={model} />
        <Row label="mode" value={mode} />
        <Row label="tools" value={String(tools)} />
        {dir ? <Row label="cwd" value={dir} /> : null}
      </Box>
      <Box marginTop={1}>
        <Text color={theme.dim}>{'Type a message · '}</Text>
        <Text color={theme.accent}>/help</Text>
        <Text color={theme.dim}>{' for commands · '}</Text>
        <Text color={theme.accent}>^C</Text>
        <Text color={theme.dim}>{' to quit'}</Text>
      </Box>
    </Box>
  )
}

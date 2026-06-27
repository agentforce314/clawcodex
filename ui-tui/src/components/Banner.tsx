/**
 * Welcome / session panel — ported from the original Claude Code startup screen
 * (typescript/src/components/StartupScreen.ts): a ✦ tagline, a double-border
 * info box (dim padEnd labels + accent/default values), a `● Ready — type /help
 * to begin` line, and a dim+accent version line.
 */
import { Box, Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'
import { Logo } from './Logo.js'

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

function Row({ label, value, accent }: { label: string; value: string; accent?: boolean }): React.ReactElement {
  return (
    <Text>
      <Text color={theme.dim}>{` ${label.padEnd(9)}`}</Text>
      <Text color={accent ? theme.accent : theme.assistant}>{value}</Text>
    </Text>
  )
}

export function Banner({ model, mode, tools, cwd }: Props): React.ReactElement {
  const dir = shorten(cwd)
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box marginTop={1} marginBottom={1}>
        <Logo />
      </Box>
      <Text>
        <Text color={theme.accent}>✦</Text>
        <Text color={theme.dim}>{'  Any model. Every tool. Zero limits.  '}</Text>
        <Text color={theme.accent}>✦</Text>
      </Text>

      <Box flexDirection="column" borderStyle="round" borderColor="rgb(130,95,75)" marginTop={1}>
        <Row label="model" value={model} accent />
        <Row label="mode" value={mode} />
        <Row label="tools" value={String(tools)} />
        {dir ? <Row label="cwd" value={dir} /> : null}
        <Text> </Text>
        <Text>
          <Text color={theme.accent}>{' ● '}</Text>
          <Text color={theme.dim}>{'local    Ready — type '}</Text>
          <Text color={theme.accent}>/help</Text>
          <Text color={theme.dim}>{' to begin'}</Text>
        </Text>
      </Box>
      <Text>
        <Text color={theme.dim}>{'  clawcodex-tui '}</Text>
        <Text color={theme.accent}>v0.1</Text>
      </Text>
    </Box>
  )
}

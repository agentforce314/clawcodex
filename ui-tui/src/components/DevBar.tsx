/**
 * DevBar (the original's dev-build status bar, §7) — adapted: a compact internal-
 * state line for debugging the TUI, shown only when CLAWCODEX_DEV=1 so it never
 * clutters normal use. Renders nothing otherwise.
 */
import { Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'

interface Props {
  entries: number
  agents: number
  busy: boolean
  stream: number
  scroll: number
  fullscreen: boolean
}

export function DevBar({ entries, agents, busy, stream, scroll, fullscreen }: Props): React.ReactElement | null {
  if (process.env['CLAWCODEX_DEV'] !== '1') return null
  return (
    <Text color={theme.dim}>
      {`[dev] entries:${entries} agents:${agents} busy:${busy ? 1 : 0} stream:${stream} scroll:${scroll} ${fullscreen ? 'fs' : 'inline'}`}
    </Text>
  )
}

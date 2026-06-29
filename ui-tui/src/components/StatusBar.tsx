/**
 * Bottom footer — matches the original PromptInputFooter: left side shows a
 * hint ("? for shortcuts"), right side shows status — context-window usage (the
 * original's StatusLine context %), a connection dot, and model · mode (mode
 * colored when non-default, like getModeColor).
 */
import { Box, Text } from '../ink.js'
import React from 'react'
import { theme } from '../theme.js'

interface ContextUsage {
  percentage: number
  totalTokens: number
  maxTokens: number
}

interface Props {
  connected: boolean
  model: string
  mode: string
  busy: boolean
  context?: ContextUsage | null
  cost?: number
  fast?: boolean // FastIcon (§7): ⚡ when fast mode is on
  effort?: string // EffortCallout (§7): reasoning effort level when set
  prBadge?: string // PrBadge (§7): current branch's PR (e.g. "#42")
}

const MODE_COLOR: Record<string, string | undefined> = {
  default: undefined,
  acceptEdits: theme.success,
  bypassPermissions: theme.error,
  plan: 'rgb(72,150,140)', // sage (planMode)
}

/** Compact token count: 1234 → "1.2k", 200000 → "200k". */
function fmtK(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 100_000 ? 0 : 1)}k`
  return String(n)
}

/** Context % colored by headroom: dim plenty, amber tightening, red low. */
function ctxColor(pct: number): string {
  if (pct >= 90) return theme.error
  if (pct >= 70) return theme.warn
  return theme.dim
}

export function StatusBar({ connected, model, mode, busy, context, cost, fast, effort, prBadge }: Props): React.ReactElement {
  const dot = !connected ? theme.dim : busy ? theme.warn : theme.success
  const modeColor = MODE_COLOR[mode] ?? theme.dim
  return (
    <Box marginTop={1} justifyContent="space-between">
      <Text color={theme.dim}>? for shortcuts</Text>
      <Box>
        {cost && cost > 0 ? (
          <Text color={theme.dim}>{`${cost < 0.01 ? `$${cost.toFixed(4)}` : `$${cost.toFixed(2)}`} · `}</Text>
        ) : null}
        {context ? (
          <>
            <Text color={ctxColor(context.percentage)}>{`${Math.round(context.percentage)}%`}</Text>
            <Text color={theme.dim}>{` (${fmtK(context.totalTokens)}/${fmtK(context.maxTokens)}) · `}</Text>
          </>
        ) : null}
        {prBadge ? <Text color={theme.dim}>{`⊟ ${prBadge} · `}</Text> : null}
        {fast ? <Text color={theme.accent}>{'⚡ '}</Text> : null}
        <Text color={dot}>{'● '}</Text>
        <Text color={theme.dim}>{model}</Text>
        <Text color={theme.dim}>{' · '}</Text>
        <Text color={modeColor}>{mode}</Text>
        {effort ? <Text color={theme.dim}>{` · ${effort}`}</Text> : null}
      </Box>
    </Box>
  )
}

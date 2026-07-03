/**
 * Footer row below the composer's bottom rule — the original PromptInput
 * footer (PromptInputFooterLeftSide + ModeIndicator):
 *
 *   ? for shortcuts                                  ⏸ plan mode on (shift+tab to cycle)
 *
 * Left byline precedence: bash-mode hint → busy interrupt hint → idle
 * `? for shortcuts`. Right side: the permission-mode badge (plan sage /
 * accept-edits violet / bypass red / auto amber) and the voice indicator when
 * active. Everything hides while the input has text (suppressHint).
 */
import { Box, Text } from '@clawcodex/ink'
import { memo } from 'react'

import type { Theme } from '../theme.js'

interface ModeBadge {
  color: (t: Theme) => string
  label: string
  symbol: string
}

// Original PermissionMode.ts symbols/titles: ⏸ (U+23F8) for plan, ▶▶
// (U+25B6 ×2) for the rest.
const MODE_BADGES: Record<string, ModeBadge> = {
  acceptEdits: { color: t => t.color.autoAccept, label: 'accept edits on', symbol: '▶▶' },
  auto: { color: t => t.color.warn, label: 'auto mode on', symbol: '▶▶' },
  bypassPermissions: { color: t => t.color.error, label: 'bypass permissions on', symbol: '▶▶' },
  dontAsk: { color: t => t.color.error, label: "don't ask on", symbol: '▶▶' },
  plan: { color: t => t.color.planMode, label: 'plan mode on', symbol: '⏸' }
}

export const ComposerFooter = memo(function ComposerFooter({
  busy,
  inputEmpty,
  mode,
  sh,
  t,
  voiceLabel = ''
}: ComposerFooterProps) {
  // CC suppressHint: nothing while the user is typing.
  if (!inputEmpty) {
    return null
  }

  const badge = MODE_BADGES[mode]
  // Voice shows only when actually active — the StatusRule's label starts
  // with ●/◉ while recording/transcribing and reads "voice off" otherwise.
  const voiceActive = /^[●◉]/.test(voiceLabel)

  // Left hint is independent of the badge (both render, like the original's
  // byline; the badge just lives on the right here).
  const left = sh ? (
    <Text color={t.color.bashBorder}>! for bash mode</Text>
  ) : busy ? (
    <Text color={t.color.muted} dim>
      esc to interrupt
    </Text>
  ) : (
    <Text color={t.color.muted} dim>
      ? for shortcuts
    </Text>
  )

  const right = (
    <>
      {voiceActive && (
        <Text color={t.color.muted} dim>
          {voiceLabel}
          {badge ? ' · ' : ''}
        </Text>
      )}
      {badge && (
        <Text color={badge.color(t)}>
          {badge.symbol} {badge.label}
          <Text color={t.color.muted} dim>
            {' (shift+tab to cycle)'}
          </Text>
        </Text>
      )}
    </>
  )

  return (
    <Box justifyContent="space-between" paddingX={2}>
      <Box>{left ?? <Text> </Text>}</Box>
      <Box>{right}</Box>
    </Box>
  )
})

interface ComposerFooterProps {
  busy: boolean
  inputEmpty: boolean
  mode: string
  sh: boolean
  t: Theme
  /** StatusRule-style voice label ("voice off" / "● rec 0:04" / "◉ …"). */
  voiceLabel?: string
}

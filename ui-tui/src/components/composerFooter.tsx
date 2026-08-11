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

import { useTurnSelector } from '../app/turnStore.js'
import type { Theme } from '../theme.js'

interface ModeBadge {
  color: (t: Theme) => string
  label: string
  symbol: string
}

// Original PermissionMode.ts symbols/titles: ⏸ (U+23F8) for plan, ▶▶
// (U+25B6 ×2) for the rest.
// Labels track the /permissions picker's vocabulary (lib/permissionLevels.ts)
// so the badge and the picker never name the same mode two different ways.
// `plan` / `dontAsk` / `auto` have no picker row and keep their own wording.
export const MODE_BADGES: Record<string, ModeBadge> = {
  acceptEdits: { color: t => t.color.autoAccept, label: 'approve for me on', symbol: '▶▶' },
  auto: { color: t => t.color.warn, label: 'auto mode on', symbol: '▶▶' },
  bypassPermissions: { color: t => t.color.error, label: 'full access on', symbol: '▶▶' },
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
  // Reads the store directly like BusyLine/LiveTodoPanel — the hint must
  // track live todo state, and hooks must run before any early return.
  const todoCount = useTurnSelector(state => state.todos.length)
  const todoCollapsed = useTurnSelector(state => state.todoCollapsed)

  // CC suppressHint: nothing while the user is typing.
  if (!inputEmpty) {
    return null
  }

  const badge = MODE_BADGES[mode]
  // Voice shows only when actually active — the StatusRule's label starts
  // with ●/◉ while recording/transcribing and reads "voice off" otherwise.
  const voiceActive = /^[●◉]/.test(voiceLabel)

  // The original's toggle segment (PromptInputFooterLeftSide
  // getSpinnerHintParts): gated on tasks EXISTING, not on busy — only the
  // esc segment is loading-gated (:522). That matters here because an
  // incomplete list now stays pinned while idle, and idle is exactly when a
  // user stares at the panel wanting to know how to hide it.
  const todoHint = todoCount > 0 ? ` · ctrl+t to ${todoCollapsed ? 'show' : 'hide'} tasks` : ''

  // Left hint is independent of the badge (both render, like the original's
  // byline; the badge just lives on the right here).
  const left = sh ? (
    <Text color={t.color.bashBorder}>! for bash mode</Text>
  ) : busy ? (
    <Text color={t.color.muted} dim>
      esc to interrupt{todoHint}
    </Text>
  ) : (
    <Text color={t.color.muted} dim>
      ? for shortcuts{todoHint}
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

/**
 * Exit-time keep/remove dialog for a --worktree session — the TS reference's
 * WorktreeExitDialog (options, default-to-keep, Esc-cancels-exit, interim
 * keeping/removing state) on the clawcodex prompt-overlay chassis
 * (ConfirmPrompt's select styling).
 */
import { Box, Text, useInput } from '@clawcodex/ink'
import { useState } from 'react'

import type { Theme } from '../theme.js'
import type { WorktreeExitReq } from '../types.js'

interface WorktreeExitPromptProps {
  req: WorktreeExitReq
  t: Theme
}

export function WorktreeExitPrompt({ req, t }: WorktreeExitPromptProps) {
  // 0 = keep (default focus, TS parity), 1 = remove.
  const [sel, setSel] = useState(0)

  const busy = req.phase !== 'asking'

  useInput((ch, key) => {
    const lower = ch.toLowerCase()

    if (busy) {
      // A hung `git worktree remove` would otherwise lock the UI for the full
      // RPC deadline (10 min) with no escape — Ctrl+C/Ctrl+D dies immediately
      // (worktree left in place, the safe direction).
      if (key.ctrl && (lower === 'c' || lower === 'd')) {
        req.onForceQuit()
      }

      return
    }

    if (key.escape || (key.ctrl && lower === 'c')) {
      return req.onCancel()
    }

    if (lower === 'k') {
      return req.onChoose('keep')
    }

    if (lower === 'r') {
      return req.onChoose('remove')
    }

    if (key.upArrow) {
      setSel(0)
    }

    if (key.downArrow) {
      setSel(1)
    }

    if (key.return) {
      req.onChoose(sel === 0 ? 'keep' : 'remove')
    }
  })

  if (busy) {
    return (
      <Box borderColor={t.color.border} borderStyle="double" flexDirection="column" paddingX={1}>
        <Text color={t.color.warn}>{req.phase === 'keeping' ? 'Keeping worktree…' : 'Removing worktree…'}</Text>
        <Text color={t.color.muted}>Ctrl+C to quit without waiting (worktree left in place)</Text>
      </Box>
    )
  }

  const accent = req.removeIsDanger ? t.color.warn : t.color.accent

  const rows = [
    { color: t.color.text, desc: `Stays at ${req.path}`, label: 'Keep worktree' },
    {
      color: req.removeIsDanger ? t.color.error : t.color.text,
      desc: req.removeIsDanger ? 'All changes and commits will be lost.' : 'Clean up the worktree directory.',
      label: 'Remove worktree'
    }
  ]

  return (
    <Box borderColor={accent} borderStyle="double" flexDirection="column" paddingX={1}>
      <Text bold color={accent}>
        ? Exiting worktree session
      </Text>

      <Box paddingLeft={1}>
        <Text color={t.color.text} wrap="wrap">
          {req.subtitle}
        </Text>
      </Box>

      <Text />

      {rows.map((row, i) => (
        <Box flexDirection="column" key={row.label}>
          <Text>
            <Text color={sel === i ? accent : t.color.muted}>{sel === i ? '▸ ' : '  '}</Text>
            <Text bold={sel === i} color={sel === i ? row.color : t.color.muted}>
              {row.label}
            </Text>
          </Text>
          <Text color={t.color.muted} wrap="truncate-end">
            {'    '}
            {row.desc}
          </Text>
        </Box>
      ))}

      <Text color={t.color.muted}>↑/↓ select · Enter confirm · K/R quick · Esc back to session</Text>
    </Box>
  )
}

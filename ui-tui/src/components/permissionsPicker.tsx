import { Box, Text, useInput } from '@clawcodex/ink'
import { useState } from 'react'

import { levelForMode, PERMISSION_LEVELS } from '../lib/permissionLevels.js'
import type { Theme } from '../theme.js'

import { OverlayHint } from './overlayControls.js'

/**
 * Interactive `/permissions` picker — the three levels from
 * `lib/permissionLevels.ts` (Python twin: `src/permissions/levels.py`).
 *
 * Shaped on `LogoPicker`: Enter hands the chosen level key to `onSelect`, which
 * re-enters `/permissions <key>` so the result lands in the transcript and the
 * single slash-command path owns the RPC. Esc cancels. Rows are numbered and
 * `1`–`3` select directly, matching the reference picker.
 *
 * `current` is the live engine mode, not a level key: `plan` / `dontAsk` are
 * real modes with no row here, so `levelForMode` returns undefined and no row
 * is marked current — deliberately, rather than mislabeling plan mode as one of
 * the three.
 */
export function PermissionsPicker({ current, onClose, onSelect, t }: PermissionsPickerProps) {
  const currentLevel = levelForMode(current)
  const currentIdx = currentLevel ? PERMISSION_LEVELS.findIndex(l => l.key === currentLevel.key) : -1
  const [idx, setIdx] = useState(Math.max(0, currentIdx))

  useInput((input, key) => {
    if (key.escape) {
      return onClose()
    }

    if (key.upArrow) {
      return setIdx(i => Math.max(0, i - 1))
    }

    if (key.downArrow) {
      return setIdx(i => Math.min(PERMISSION_LEVELS.length - 1, i + 1))
    }

    // Direct 1/2/3 selection — applies immediately, like the reference picker.
    const digit = Number.parseInt(input, 10)

    if (Number.isInteger(digit) && digit >= 1 && digit <= PERMISSION_LEVELS.length) {
      const picked = PERMISSION_LEVELS[digit - 1]

      return picked ? onSelect(picked.key) : undefined
    }

    if (key.return) {
      const picked = PERMISSION_LEVELS[idx]

      return picked ? onSelect(picked.key) : undefined
    }
  })

  return (
    <Box flexDirection="column">
      <Text bold color={t.color.accent}>
        Choose what clawcodex is allowed to do
      </Text>

      {PERMISSION_LEVELS.map((level, i) => {
        const at = i === idx
        const isCurrent = currentLevel?.key === level.key

        return (
          <Box flexDirection="column" key={level.key}>
            <Text wrap="truncate-end">
              <Text bold={at} color={at ? t.color.accent : t.color.muted}>
                {at ? '▸ ' : '  '}
                {i + 1}.{' '}
              </Text>
              <Text bold={at} color={at ? t.color.accent : t.color.text}>
                {level.label}
              </Text>
              {isCurrent && <Text color={t.color.muted}> · current</Text>}
            </Text>
            <Text color={t.color.muted} dim wrap="wrap">
              {'     '}
              {level.description}
            </Text>
          </Box>
        )
      })}

      <OverlayHint t={t}>↑/↓ or 1-3 select · Enter apply · Esc cancel</OverlayHint>
    </Box>
  )
}

interface PermissionsPickerProps {
  /** The live engine permission mode (not a level key). */
  current: string
  onClose: () => void
  /** Receives the chosen level KEY (`ask` / `approve` / `full`). */
  onSelect: (key: string) => void
  t: Theme
}

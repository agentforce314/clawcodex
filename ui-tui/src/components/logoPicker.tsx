import { Box, Text, useInput } from '@clawcodex/ink'
import { useState } from 'react'

import { wordmarkGradient } from '../banner.js'
import {
  DEFAULT_LOGO_PALETTE,
  isLogoPaletteName,
  LOGO_PALETTE_LABELS,
  LOGO_PALETTE_NAMES,
  type LogoPaletteName
} from '../lib/logoPalettes.js'
import type { Theme } from '../theme.js'

import { OverlayHint } from './overlayControls.js'

/**
 * Interactive /logo picker overlay — port of openclaude's `LogoPicker`
 * (`components/LogoPicker.tsx`). Each option carries a six-block preview
 * swatch (one ▇ per gradient stop, the original's `previewSwatch`); the
 * swatch colors come from `wordmarkGradient` so the preview is exactly what
 * the banner will paint on the next launch. Enter hands the chosen name to
 * `onSelect` (the app re-enters `/logo <name>`, the /model picker pattern);
 * Esc cancels.
 */
export function LogoPicker({ current, onClose, onSelect, t }: LogoPickerProps) {
  const initial = isLogoPaletteName(current) ? current : DEFAULT_LOGO_PALETTE
  const [idx, setIdx] = useState(Math.max(0, LOGO_PALETTE_NAMES.indexOf(initial)))

  useInput((_input, key) => {
    if (key.escape) {
      return onClose()
    }

    if (key.upArrow) {
      return setIdx(i => Math.max(0, i - 1))
    }

    if (key.downArrow) {
      return setIdx(i => Math.min(LOGO_PALETTE_NAMES.length - 1, i + 1))
    }

    if (key.return) {
      const name = LOGO_PALETTE_NAMES[idx]

      return name ? onSelect(name) : undefined
    }
  })

  return (
    <Box flexDirection="column">
      <Text bold color={t.color.accent}>
        Choose the startup logo color scheme
      </Text>

      {LOGO_PALETTE_NAMES.map((name, i) => {
        const at = i === idx

        return (
          <Text key={name} wrap="truncate-end">
            <Text bold={at} color={at ? t.color.accent : t.color.muted}>
              {at ? '▸ ' : '  '}
            </Text>
            {wordmarkGradient(name).map((color, s) => (
              <Text color={color} key={s}>
                ▇
              </Text>
            ))}
            <Text bold={at} color={at ? t.color.accent : t.color.text} inverse={at}>
              {'  '}
              {LOGO_PALETTE_LABELS[name]}
            </Text>
            {name === initial && <Text color={t.color.muted}> · current</Text>}
          </Text>
        )
      })}

      <OverlayHint t={t}>↑/↓ select · Enter apply · Esc cancel</OverlayHint>
    </Box>
  )
}

interface LogoPickerProps {
  /** The active palette name ('' = default sunset). */
  current: string
  onClose: () => void
  onSelect: (name: LogoPaletteName) => void
  t: Theme
}

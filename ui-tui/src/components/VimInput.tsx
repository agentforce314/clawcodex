/**
 * Minimal vim modal input (the original's /vim), used in place of ink-text-input
 * only when vim mode is on — so the default input path is untouched. Edits the
 * shared `value` (so the slash/@ menus + history still work off it) and owns a
 * cursor + normal/insert mode via its own useInput (active only when no menu /
 * dialog is up, so App's menu navigation keeps working).
 *
 * Normal: h/l/0/$ move · w/b word · i/a/I/A insert · x delete · D kill-to-end ·
 *   Enter submit.  Insert: type · ←/→ · Backspace · Esc→normal · Enter submit.
 */
import { Text, useInput } from 'ink'
import React, { useState } from 'react'
import { theme } from '../theme.js'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: (v: string) => void
  active: boolean
  placeholder?: string
}

const WORD = /[\p{L}\p{N}_]/u
function nextWord(s: string, c: number): number {
  let i = c
  while (i < s.length && WORD.test(s[i] ?? '')) i++ // end current word
  while (i < s.length && !WORD.test(s[i] ?? '')) i++ // skip gap
  return i
}
function prevWord(s: string, c: number): number {
  let i = c - 1
  while (i > 0 && !WORD.test(s[i] ?? '')) i-- // skip gap
  while (i > 0 && WORD.test(s[i - 1] ?? '')) i-- // to word start
  return Math.max(0, i)
}

export function VimInput({ value, onChange, onSubmit, active, placeholder }: Props): React.ReactElement {
  const [normal, setNormal] = useState(false) // start in insert (like the original entering /vim)
  const [cursor, setCursor] = useState(value.length)
  const clamp = (c: number, max = value.length): number => Math.max(0, Math.min(max, c))

  useInput(
    (input, key) => {
      if (!active) return
      if (normal) {
        if (input === 'i') return setNormal(false)
        if (input === 'a') {
          setCursor((c) => clamp(c + 1))
          return setNormal(false)
        }
        if (input === 'A') {
          setCursor(value.length)
          return setNormal(false)
        }
        if (input === 'I') {
          setCursor(0)
          return setNormal(false)
        }
        if (input === 'h' || key.leftArrow) return setCursor((c) => clamp(c - 1))
        if (input === 'l' || key.rightArrow) return setCursor((c) => clamp(c + 1))
        if (input === '0') return setCursor(0)
        if (input === '$') return setCursor(Math.max(0, value.length - 1))
        if (input === 'w') return setCursor(nextWord(value, cursor))
        if (input === 'b') return setCursor(prevWord(value, cursor))
        if (input === 'x') {
          if (value) {
            onChange(value.slice(0, cursor) + value.slice(cursor + 1))
            setCursor((c) => clamp(c, Math.max(0, value.length - 2)))
          }
          return
        }
        if (input === 'D') return onChange(value.slice(0, cursor))
        if (key.return) return onSubmit(value)
        return // normal mode swallows everything else
      }
      // insert mode
      if (key.escape) {
        setNormal(true)
        setCursor((c) => Math.max(0, c - 1))
        return
      }
      if (key.return) return onSubmit(value)
      if (key.leftArrow) return setCursor((c) => clamp(c - 1))
      if (key.rightArrow) return setCursor((c) => clamp(c + 1))
      if (key.backspace || key.delete) {
        if (cursor > 0) {
          onChange(value.slice(0, cursor - 1) + value.slice(cursor))
          setCursor((c) => c - 1)
        }
        return
      }
      if (input && !key.ctrl && !key.meta) {
        onChange(value.slice(0, cursor) + input + value.slice(cursor))
        setCursor((c) => c + input.length)
      }
    },
    { isActive: active },
  )

  // Render: mode tag + text with a block cursor (reverse video).
  const cur = clamp(cursor)
  const empty = value.length === 0
  const tag = normal ? '[N] ' : '[I] '
  if (empty && placeholder) {
    return (
      <Text>
        <Text color={normal ? theme.warn : theme.accent}>{tag}</Text>
        <Text inverse> </Text>
        <Text color={theme.dim}>{placeholder}</Text>
      </Text>
    )
  }
  const before = value.slice(0, cur)
  const at = value.slice(cur, cur + 1) || ' '
  const after = value.slice(cur + 1)
  return (
    <Text>
      <Text color={normal ? theme.warn : theme.accent}>{tag}</Text>
      <Text>{before}</Text>
      <Text inverse>{at}</Text>
      <Text>{after}</Text>
    </Text>
  )
}

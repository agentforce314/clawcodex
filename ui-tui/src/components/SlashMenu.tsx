/**
 * Slash-command suggestions shown above the input — matches the original
 * PromptInputFooterSuggestions: a borderless list where the selected row is a
 * full-width highlighted bar (background + inverse text), format
 * "/name   description".
 *
 * The list is WINDOWED to MAX_VISIBLE rows around the selection. A full ~70-row
 * list overflows the terminal's live region; when it closes, Ink can't clear the
 * rows that scrolled into permanent scrollback, leaving the menu "pinned" on
 * screen after a command is picked. Keeping the region small clears reliably.
 */
import { Box, Text } from '../ink.js'
import React from 'react'
import { theme } from '../theme.js'
import type { SlashCommand } from '../slashCommands.js'

interface Props {
  matches: SlashCommand[]
  selected: number
}

const MAX_VISIBLE = 10

export function SlashMenu({ matches, selected }: Props): React.ReactElement | null {
  if (matches.length === 0) return null
  const total = matches.length
  // Scroll a fixed-size window so the selected row stays in view.
  const start =
    total > MAX_VISIBLE
      ? Math.max(0, Math.min(selected - Math.floor(MAX_VISIBLE / 2), total - MAX_VISIBLE))
      : 0
  const visible = matches.slice(start, start + MAX_VISIBLE)
  const nameW = Math.max(...visible.map((c) => c.name.length))
  const moreAbove = start
  const moreBelow = total - (start + visible.length)
  return (
    <Box flexDirection="column" marginBottom={1}>
      {moreAbove > 0 ? <Text color={theme.dim}>{`  ↑ ${moreAbove} more`}</Text> : null}
      {visible.map((c, i) => {
        const idx = start + i
        const on = idx === selected
        if (on) {
          // Selected: full-width highlighted bar, › prefix, dark bold text.
          return (
            <Box key={c.name} width="100%">
              <Text backgroundColor={theme.suggestion} color="black" bold wrap="truncate">
                {`› ${c.name.padEnd(nameW)}   ${c.description} `}
              </Text>
            </Box>
          )
        }
        return (
          <Box key={c.name}>
            <Text>{`  ${c.name.padEnd(nameW)}`}</Text>
            <Text color={theme.dim}>{`   ${c.description}`}</Text>
          </Box>
        )
      })}
      {moreBelow > 0 ? <Text color={theme.dim}>{`  ↓ ${moreBelow} more`}</Text> : null}
    </Box>
  )
}

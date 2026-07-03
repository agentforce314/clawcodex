import type { Theme } from '../theme.js'
import type { Role } from '../types.js'

// Original Claude Code glyphs/colors (messages/*.tsx): assistant prose gets a
// plain text-white ⏺ (AssistantTextMessage), the user echo is a near-invisible
// `❯` in `subtle` with white text (HighlightedThinkingText figures.pointer +
// pointerColor "subtle"), tools keep the green ⏺ result coloring.
export const ROLE: Record<Role, (t: Theme) => { body: string; glyph: string; prefix: string }> = {
  assistant: t => ({ body: t.color.text, glyph: '⏺', prefix: t.color.text }),
  system: t => ({ body: '', glyph: '·', prefix: t.color.muted }),
  tool: t => ({ body: t.color.muted, glyph: '⏺', prefix: t.color.ok }),
  user: t => ({ body: t.color.text, glyph: '❯', prefix: t.color.subtle })
}

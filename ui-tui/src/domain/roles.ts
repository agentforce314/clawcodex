import type { Theme } from '../theme.js'
import type { Role } from '../types.js'

// Claude Code glyphs: › user, ⏺ assistant/tool bullet, · system. The orange ⏺
// marks the assistant; a green ⏺ marks a tool call (Claude's status coloring).
export const ROLE: Record<Role, (t: Theme) => { body: string; glyph: string; prefix: string }> = {
  assistant: t => ({ body: t.color.text, glyph: '⏺', prefix: t.color.accent }),
  system: t => ({ body: '', glyph: '·', prefix: t.color.muted }),
  tool: t => ({ body: t.color.muted, glyph: '⏺', prefix: t.color.ok }),
  user: t => ({ body: t.color.label, glyph: '›', prefix: t.color.muted })
}

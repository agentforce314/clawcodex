/**
 * Color palette + glyphs for the Ink TUI. Kept small and centralized so the
 * look stays consistent across components — the Claude-Code-style "feel" lives
 * here (markers, dim chrome, accent cyan, green code).
 */
export const theme = {
  user: 'cyan',
  assistant: undefined as string | undefined, // terminal default fg
  tool: 'yellow',
  toolResult: 'gray',
  system: 'gray',
  error: 'red',
  success: 'green',
  warn: 'yellow',
  dim: 'gray',
  accent: 'cyan',
  heading: 'magentaBright',
  code: 'green',
  link: 'blueBright',
  border: 'gray',
  spinner: 'cyan',
} as const

/** Leading glyphs for each message kind (Claude-Code-ish). */
export const glyph = {
  user: '› ',
  assistant: '⏺ ',
  tool: '⏺ ',
  toolResult: '  ⎿ ',
  system: '· ',
  result: '✓ ',
  error: '✗ ',
} as const

/**
 * Color palette — the EXACT Claude Code dark theme, ported from
 * typescript/src/utils/theme.ts (`darkTheme`). Ink accepts `rgb(r,g,b)` for
 * truecolor, so we use the real brand values rather than approximate ANSI
 * names. The signature element is Claude orange `rgb(215,119,87)`.
 */
export const theme = {
  user: undefined as string | undefined, // user text: default (white)
  assistant: undefined as string | undefined, // assistant text + ⏺ dot: default white ("text")
  tool: 'rgb(215,119,87)', // tool name — claude orange
  toolResult: 'rgb(153,153,153)', // inactive (secondary text)
  system: 'rgb(153,153,153)',
  error: 'rgb(255,107,128)', // bright red
  success: 'rgb(78,186,101)', // bright green
  warn: 'rgb(255,193,7)', // bright amber
  dim: 'rgb(153,153,153)', // inactive — secondary text
  subtle: 'rgb(80,80,80)', // very dim — gutters / faint borders
  accent: 'rgb(215,119,87)', // claude orange (brand)
  brand: 'rgb(215,119,87)',
  heading: undefined as string | undefined, // headings: bold, default color
  code: 'rgb(78,186,101)', // fallback code color (cli-highlight overrides)
  link: 'rgb(177,185,249)', // light blue-purple
  suggestion: 'rgb(177,185,249)',
  border: 'rgb(80,80,80)', // subtle box borders
  promptBorder: 'rgb(136,136,136)', // input rule
  userBg: 'rgb(55,55,55)', // user message background
  spinner: 'rgb(215,119,87)', // claude orange
  spinnerShimmer: 'rgb(235,159,127)', // lighter claude orange — spinner glimmer sweep
  diffAddBg: 'rgb(34,92,43)', // dark green — added lines
  diffDelBg: 'rgb(122,41,54)', // dark red — removed lines
} as const

/** Leading glyphs for each message kind (Claude-Code figures). */
export const glyph = {
  user: '› ',
  assistant: '⏺ ',
  tool: '⏺ ',
  toolResult: '  ⎿ ',
  system: '· ',
  result: '✓ ',
  error: '✗ ',
} as const

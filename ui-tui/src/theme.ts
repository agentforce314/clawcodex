/**
 * Color palette — ported from typescript/src/utils/theme.ts (darkTheme +
 * lightTheme). Ink accepts `rgb(r,g,b)` for truecolor, so we use the real brand
 * values. The signature element is Claude orange `rgb(215,119,87)`.
 *
 * `theme` is a MUTABLE live object: every component reads `theme.*` at render
 * time, so `applyTheme(name)` (from /theme or $CLAWCODEX_THEME) swaps the
 * palette in place and a top-level re-render repaints the dynamic UI + all new
 * output. Already-printed scrollback keeps its colors — like a real terminal.
 */

export interface ThemeTokens {
  user: string | undefined
  assistant: string | undefined
  tool: string
  toolResult: string
  system: string
  error: string
  success: string
  warn: string
  dim: string
  subtle: string
  accent: string
  brand: string
  heading: string | undefined
  code: string
  link: string
  suggestion: string
  border: string
  promptBorder: string
  userBg: string
  spinner: string
  spinnerShimmer: string
  diffAddBg: string
  diffDelBg: string
}

const DARK: ThemeTokens = {
  user: undefined,
  assistant: undefined,
  tool: 'rgb(215,119,87)',
  toolResult: 'rgb(153,153,153)',
  system: 'rgb(153,153,153)',
  error: 'rgb(255,107,128)',
  success: 'rgb(78,186,101)',
  warn: 'rgb(255,193,7)',
  dim: 'rgb(153,153,153)',
  subtle: 'rgb(80,80,80)',
  accent: 'rgb(215,119,87)',
  brand: 'rgb(215,119,87)',
  heading: undefined,
  code: 'rgb(78,186,101)',
  link: 'rgb(177,185,249)',
  suggestion: 'rgb(177,185,249)',
  border: 'rgb(80,80,80)',
  promptBorder: 'rgb(136,136,136)',
  userBg: 'rgb(55,55,55)',
  spinner: 'rgb(215,119,87)',
  spinnerShimmer: 'rgb(235,159,127)',
  diffAddBg: 'rgb(34,92,43)',
  diffDelBg: 'rgb(122,41,54)',
}

const LIGHT: ThemeTokens = {
  user: undefined,
  assistant: undefined,
  tool: 'rgb(215,119,87)',
  toolResult: 'rgb(102,102,102)',
  system: 'rgb(102,102,102)',
  error: 'rgb(171,43,63)',
  success: 'rgb(44,122,57)',
  warn: 'rgb(150,108,30)',
  dim: 'rgb(102,102,102)',
  subtle: 'rgb(175,175,175)',
  accent: 'rgb(215,119,87)',
  brand: 'rgb(215,119,87)',
  heading: undefined,
  code: 'rgb(44,122,57)',
  link: 'rgb(87,105,247)',
  suggestion: 'rgb(87,105,247)',
  border: 'rgb(175,175,175)',
  promptBorder: 'rgb(153,153,153)',
  userBg: 'rgb(240,240,240)',
  spinner: 'rgb(215,119,87)',
  spinnerShimmer: 'rgb(245,149,117)',
  diffAddBg: 'rgb(105,219,124)',
  diffDelBg: 'rgb(255,168,180)',
}

/** Theme palettes by name. The name also drives ColorDiff (it builds its own
 *  RGB from a theme name: `dark` → Monokai + dark tints, light → GitHub). */
export const THEMES: Record<string, ThemeTokens> = {
  dark: DARK,
  light: LIGHT,
  'dark-ansi': DARK,
  'light-ansi': LIGHT,
}

/** Mutable live palette — components read this each render. */
export const theme: ThemeTokens = { ...DARK }

let _current = 'dark'

/** Current theme name (passed to ColorDiff so diffs follow the theme). */
export function currentThemeName(): string {
  return _current
}

/** Swap the live palette in place. Returns false for an unknown name. */
export function applyTheme(name: string): boolean {
  const t = THEMES[name]
  if (!t) return false
  Object.assign(theme, t)
  _current = name
  return true
}

// Honor $CLAWCODEX_THEME at startup (e.g. CLAWCODEX_THEME=light).
const envTheme = process.env['CLAWCODEX_THEME']
if (envTheme && THEMES[envTheme]) applyTheme(envTheme)

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

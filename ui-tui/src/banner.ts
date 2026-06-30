import type { ThemeColors } from './theme.js'

const RICH_RE = /\[(?:bold\s+)?(?:dim\s+)?(#(?:[0-9a-fA-F]{3,8}))\]([\s\S]*?)(\[\/\])/g

export function parseRichMarkup(markup: string): Line[] {
  const lines: Line[] = []

  for (const raw of markup.split('\n')) {
    const trimmed = raw.trimEnd()

    if (!trimmed) {
      lines.push(['', ' '])

      continue
    }

    const matches = [...trimmed.matchAll(RICH_RE)]

    if (!matches.length) {
      lines.push(['', trimmed])

      continue
    }

    let cursor = 0

    for (const m of matches) {
      const before = trimmed.slice(cursor, m.index)

      if (before) {
        lines.push(['', before])
      }

      lines.push([m[1]!, m[2]!])
      cursor = m.index! + m[0].length
    }

    if (cursor < trimmed.length) {
      lines.push(['', trimmed.slice(cursor)])
    }
  }

  return lines
}

const LOGO_ART = [
  ' ██████╗██╗      █████╗ ██╗    ██╗ ██████╗ ██████╗ ██████╗ ███████╗██╗  ██╗',
  '██╔════╝██║     ██╔══██╗██║    ██║██╔════╝██╔═══██╗██╔══██╗██╔════╝╚██╗██╔╝',
  '██║     ██║     ███████║██║ █╗ ██║██║     ██║   ██║██║  ██║█████╗   ╚███╔╝ ',
  '██║     ██║     ██╔══██║██║███╗██║██║     ██║   ██║██║  ██║██╔══╝   ██╔██╗ ',
  '╚██████╗███████╗██║  ██║╚███╔███╔╝╚██████╗╚██████╔╝██████╔╝███████╗██╔╝ ██╗',
  ' ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝'
]

// clawcodex mascot — a lobster. Rendered in the brand terracotta gradient
// (which reads as lobster-red), shown beside the session panel on the banner.
const LOBSTER_ART = [
  '(\\/)        (\\/)',
  ' \\\\__      __//',
  '   \\( o  o )/',
  '    |======|',
  '   /|======|\\',
  '   \\\\______//'
]

// Claude Code "sunset" logo gradient — warm peach down to deep terracotta,
// independent of the active theme palette so the wordmark always reads as brand.
const LOGO_SUNSET = [
  'rgb(245,166,120)',
  'rgb(233,147,107)',
  'rgb(221,128,94)',
  'rgb(208,113,84)',
  'rgb(193,102,77)',
  'rgb(178,90,70)'
] as const
// Claws/arms in accent, body in primary, tail in accent — both are the brand
// terracotta, so the whole mascot reads lobster-red.
const LOBSTER_GRADIENT = [1, 1, 0, 0, 0, 1] as const

const colorize = (art: string[], gradient: readonly number[], c: ThemeColors): Line[] => {
  const p = [c.primary, c.accent, c.border, c.muted]

  return art.map((text, i) => [p[gradient[i]!] ?? c.muted, text])
}

export const LOGO_WIDTH = Math.max(...LOGO_ART.map(line => line.length))
export const LOBSTER_WIDTH = Math.max(...LOBSTER_ART.map(line => line.length))

export const logo = (c: ThemeColors, customLogo?: string): Line[] =>
  customLogo ? parseRichMarkup(customLogo) : LOGO_ART.map((text, i) => [LOGO_SUNSET[i] ?? c.primary, text])

export const lobster = (c: ThemeColors, customHero?: string): Line[] =>
  customHero ? parseRichMarkup(customHero) : colorize(LOBSTER_ART, LOBSTER_GRADIENT, c)

export const artWidth = (lines: Line[]) => lines.reduce((m, [, t]) => Math.max(m, t.length), 0)

type Line = [string, string]

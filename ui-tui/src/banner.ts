import {
  DEFAULT_LOGO_PALETTE,
  gradientStopForRow,
  isLogoPaletteName,
  LOGO_PALETTES,
  type LogoPaletteName,
  rgbStr
} from './lib/logoPalettes.js'
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

// /logo palette → banner painting (applied at the banner's startup paint; the
// intro row is committed to scrollback, so a mid-session /logo shows on the
// NEXT launch, matching the original). The unset default AND an explicit
// "sunset" both keep the shipped look (brand LOGO_SUNSET wordmark,
// theme-colored lobster): "Sunset (default)" IS clawcodex's default scheme,
// and picking it must return exactly to it. Only a non-default palette
// changes the paint — wordmark rows one gradient stop each, lobster rows
// sampled from the same gradient so the mascot doesn't clash
// terracotta-on-ocean. Skin overrides (customLogo / customHero) win over the
// palette: a skin is a full rebrand, /logo recolors the default logo.
const nonDefaultPalette = (logoColor?: string): LogoPaletteName | null =>
  logoColor && logoColor !== DEFAULT_LOGO_PALETTE && isLogoPaletteName(logoColor) ? logoColor : null

/** The 6 wordmark row colors the banner will actually use for `logoColor` —
 *  also drives the /logo picker swatches so previews stay truthful. */
export const wordmarkGradient = (logoColor?: string): string[] => {
  const name = nonDefaultPalette(logoColor)

  return name ? LOGO_PALETTES[name].gradient.map(rgbStr) : [...LOGO_SUNSET]
}

export const logo = (c: ThemeColors, customLogo?: string, logoColor?: string): Line[] => {
  if (customLogo) {
    return parseRichMarkup(customLogo)
  }

  const grad = wordmarkGradient(logoColor)

  return LOGO_ART.map((text, i) => [grad[i] ?? c.primary, text])
}

export const lobster = (c: ThemeColors, customHero?: string, logoColor?: string): Line[] => {
  if (customHero) {
    return parseRichMarkup(customHero)
  }

  const name = nonDefaultPalette(logoColor)

  if (name) {
    const stops = LOGO_PALETTES[name].gradient

    return LOBSTER_ART.map((text, i) => [rgbStr(gradientStopForRow(stops, i, LOBSTER_ART.length)), text])
  }

  return colorize(LOBSTER_ART, LOBSTER_GRADIENT, c)
}

export const artWidth = (lines: Line[]) => lines.reduce((m, [, t]) => Math.max(m, t.length), 0)

type Line = [string, string]

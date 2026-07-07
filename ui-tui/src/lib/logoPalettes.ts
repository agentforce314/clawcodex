import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

/**
 * Color palettes for the startup banner logo, selected via /logo.
 *
 * Port of openclaude's `components/StartupScreen.palettes.ts` (values verbatim;
 * the Python twin is `src/utils/logo_palettes.py`). The chosen name persists in
 * the global config's top-level `logoColor` key (~/.clawcodex/config.json) —
 * written by the backend's `set_logo_color` control, read synchronously at TUI
 * startup by `readLogoColorSync` so the banner paints correctly on first render
 * (the original reads `getGlobalConfig().logoColor` the same way).
 */

export type RGB = readonly [number, number, number]

export interface LogoPalette {
  /** Gradient stops painted top→bottom across the ASCII logo rows. */
  gradient: readonly RGB[]
  /** Highlight color (unused by the TUI banner today; kept for parity). */
  accent: RGB
  /** Soft body text color (unused by the TUI banner today; kept for parity). */
  cream: RGB
  /** Dim color (unused by the TUI banner today; kept for parity). */
  dim: RGB
  /** Border color (unused by the TUI banner today; kept for parity). */
  border: RGB
}

export const LOGO_PALETTES = {
  sunset: {
    gradient: [
      [255, 180, 100],
      [240, 140, 80],
      [217, 119, 87],
      [193, 95, 60],
      [160, 75, 55],
      [130, 60, 50]
    ],
    accent: [240, 148, 100],
    cream: [220, 195, 170],
    dim: [120, 100, 82],
    border: [100, 80, 65]
  },
  forest: {
    gradient: [
      [180, 240, 170],
      [130, 215, 130],
      [85, 180, 95],
      [55, 145, 75],
      [40, 110, 60],
      [25, 80, 45]
    ],
    accent: [120, 200, 120],
    cream: [200, 220, 190],
    dim: [90, 120, 90],
    border: [70, 95, 70]
  },
  ocean: {
    gradient: [
      [170, 220, 255],
      [125, 185, 240],
      [80, 150, 220],
      [55, 115, 190],
      [40, 85, 150],
      [25, 55, 110]
    ],
    accent: [110, 180, 230],
    cream: [195, 215, 235],
    dim: [90, 115, 145],
    border: [70, 90, 115]
  },
  monochrome: {
    gradient: [
      [225, 225, 225],
      [195, 195, 195],
      [160, 160, 160],
      [125, 125, 125],
      [95, 95, 95],
      [70, 70, 70]
    ],
    accent: [200, 200, 200],
    cream: [210, 210, 210],
    dim: [120, 120, 120],
    border: [95, 95, 95]
  }
} as const satisfies Record<string, LogoPalette>

export type LogoPaletteName = keyof typeof LOGO_PALETTES

export const LOGO_PALETTE_NAMES = Object.keys(LOGO_PALETTES) as LogoPaletteName[]

export const DEFAULT_LOGO_PALETTE: LogoPaletteName = 'sunset'

export const LOGO_PALETTE_LABELS: Record<LogoPaletteName, string> = {
  sunset: 'Sunset (default)',
  forest: 'Forest green',
  ocean: 'Ocean blue',
  monochrome: 'Monochrome'
}

export function isLogoPaletteName(value: unknown): value is LogoPaletteName {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(LOGO_PALETTES, value)
}

/** `(r,g,b)` → the `rgb(r,g,b)` string ink's `<Text color>` accepts. */
export const rgbStr = ([r, g, b]: RGB): string => `rgb(${r},${g},${b})`

/**
 * The gradient stop for row `i` of an `n`-row art block: stops are sampled
 * evenly top→bottom (round-to-nearest), so a 6-row block over 6 stops maps
 * one stop per row. Mirrors the deleted Rich banner's `mascot_gradient_text`.
 */
export function gradientStopForRow(stops: readonly RGB[], i: number, n: number): RGB {
  if (n <= 1 || stops.length === 1) {
    return stops[0]!
  }

  return stops[Math.round((i * (stops.length - 1)) / (n - 1))]!
}

/**
 * The persisted `logoColor` palette name, or `''` when unset/invalid/unreadable.
 * Synchronous on purpose: the banner is the first transcript row and must paint
 * correctly before the backend is up (~20s cold start), exactly like the
 * original's `getGlobalConfig()` read. `CLAWCODEX_HOME` matches lib/history.ts.
 */
export function readLogoColorSync(): '' | LogoPaletteName {
  try {
    const dir = process.env.CLAWCODEX_HOME ?? join(homedir(), '.clawcodex')
    const parsed: unknown = JSON.parse(readFileSync(join(dir, 'config.json'), 'utf8'))
    const value = (parsed as { logoColor?: unknown }).logoColor

    return isLogoPaletteName(value) ? value : ''
  } catch {
    return ''
  }
}

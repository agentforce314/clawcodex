/**
 * Block-ascii "CLAWCODEX" wordmark (figlet ANSI Shadow — the same font the
 * original Claude Code logo uses) painted with a selectable gradient (the
 * original's LogoPicker, §6/§7). The palette is chosen via /logo and persisted
 * to ~/.clawcodex/logo.json.
 */
import { Box, Text } from 'ink'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, dirname } from 'node:path'
import React from 'react'

const LOGO_LINES = [
  ' ██████╗██╗      █████╗ ██╗    ██╗ ██████╗ ██████╗ ██████╗ ███████╗██╗  ██╗',
  '██╔════╝██║     ██╔══██╗██║    ██║██╔════╝██╔═══██╗██╔══██╗██╔════╝╚██╗██╔╝',
  '██║     ██║     ███████║██║ █╗ ██║██║     ██║   ██║██║  ██║█████╗   ╚███╔╝ ',
  '██║     ██║     ██╔══██║██║███╗██║██║     ██║   ██║██║  ██║██╔══╝   ██╔██╗ ',
  '╚██████╗███████╗██║  ██║╚███╔███╔╝╚██████╗╚██████╔╝██████╔╝███████╗██╔╝ ██╗',
  ' ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝',
]

/** Selectable 6-stop gradients (one color per logo line). */
export const LOGO_PALETTES: Record<string, string[]> = {
  sunset: ['rgb(245,166,120)', 'rgb(233,147,107)', 'rgb(221,128,94)', 'rgb(208,113,84)', 'rgb(193,102,77)', 'rgb(178,90,70)'],
  ocean: ['rgb(140,210,235)', 'rgb(108,184,222)', 'rgb(80,158,208)', 'rgb(58,131,190)', 'rgb(42,105,168)', 'rgb(30,82,142)'],
  forest: ['rgb(170,222,140)', 'rgb(138,200,112)', 'rgb(108,176,88)', 'rgb(82,150,68)', 'rgb(60,124,52)', 'rgb(42,98,40)'],
  berry: ['rgb(240,160,205)', 'rgb(222,128,186)', 'rgb(200,100,170)', 'rgb(174,80,158)', 'rgb(146,64,142)', 'rgb(118,50,120)'],
  gold: ['rgb(248,222,130)', 'rgb(238,200,96)', 'rgb(224,176,68)', 'rgb(206,150,48)', 'rgb(184,124,36)', 'rgb(160,100,28)'],
  mono: ['rgb(220,220,220)', 'rgb(190,190,190)', 'rgb(160,160,160)', 'rgb(130,130,130)', 'rgb(100,100,100)', 'rgb(72,72,72)'],
}

const LOGO_FILE = join(homedir(), '.clawcodex', 'logo.json')

function loadPaletteName(): string {
  try {
    const v = JSON.parse(readFileSync(LOGO_FILE, 'utf8'))
    if (v && typeof v.palette === 'string' && LOGO_PALETTES[v.palette]) return v.palette
  } catch {
    /* default */
  }
  return 'sunset'
}

let _palette = loadPaletteName()

/** Persist + apply a logo palette (no-op for an unknown name). */
export function setLogoPalette(name: string): void {
  if (!LOGO_PALETTES[name]) return
  _palette = name
  try {
    mkdirSync(dirname(LOGO_FILE), { recursive: true })
    writeFileSync(LOGO_FILE, JSON.stringify({ palette: name }), 'utf8')
  } catch {
    /* best-effort */
  }
}

export function getLogoPalette(): string {
  return _palette
}

export function Logo(): React.ReactElement {
  const gradient = LOGO_PALETTES[_palette] ?? LOGO_PALETTES['sunset']!
  return (
    <Box flexDirection="column">
      {LOGO_LINES.map((line, i) => (
        <Text key={i} color={gradient[i]}>
          {line}
        </Text>
      ))}
    </Box>
  )
}

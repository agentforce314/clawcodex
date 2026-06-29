/**
 * Buddy — the animated companion sprite ported from openclaude's src/buddy/
 * (sprites.ts). A small ASCII creature that idle-cycles its frames and blinks.
 * Sprites are 5 lines × 12 wide with {E} eye slots (substituted per blink).
 * Opt-in (the original is feature-gated); rendered above the input when on.
 */
import { Box, Text } from '../ink.js'
import React, { useEffect, useState } from 'react'
import { theme } from '../theme.js'

// Verbatim frames from typescript/src/buddy/sprites.ts (idle fidget animation).
const BODIES: Record<string, string[][]> = {
  cat: [
    ['            ', '   /\\_/\\    ', '  ( {E}   {E})  ', '  (  ω  )   ', '  (")_(")   '],
    ['            ', '   /\\_/\\    ', '  ( {E}   {E})  ', '  (  ω  )   ', '  (")_(")~  '],
    ['            ', '   /\\-/\\    ', '  ( {E}   {E})  ', '  (  ω  )   ', '  (")_(")   '],
  ],
  duck: [
    ['            ', '    __      ', '  <({E} )___  ', '   (  ._>   ', '    `--´    '],
    ['            ', '    __      ', '  <({E} )___  ', '   (  ._>   ', '    `--´~   '],
    ['            ', '    __      ', '  <({E} )___  ', '   (  .__>  ', '    `--´    '],
  ],
  blob: [
    ['            ', '   .----.   ', '  ( {E}  {E} )  ', '  (      )  ', '   `----´   '],
    ['            ', '  .------.  ', ' (  {E}  {E}  ) ', ' (        ) ', '  `------´  '],
    ['            ', '    .--.    ', '   ({E}  {E})   ', '   (    )   ', '    `--´    '],
  ],
}
export const BUDDY_SPECIES = Object.keys(BODIES)

export function CompanionSprite({ species }: { species?: string }): React.ReactElement {
  const sp = species && BODIES[species] ? species : 'cat'
  const frames = BODIES[sp] as string[][]
  const [frame, setFrame] = useState(0)
  const [blink, setBlink] = useState(false)

  useEffect(() => {
    const f = setInterval(() => setFrame((x) => (x + 1) % frames.length), 900)
    const b = setInterval(() => {
      setBlink(true)
      setTimeout(() => setBlink(false), 160)
    }, 4200)
    return () => {
      clearInterval(f)
      clearInterval(b)
    }
  }, [frames.length])

  const eye = blink ? '-' : '•'
  const lines = (frames[frame] as string[]).map((l) => l.replace(/\{E\}/g, eye))
  return (
    <Box flexDirection="column">
      {lines.map((l, i) => (
        <Text key={i} color={theme.accent}>
          {l}
        </Text>
      ))}
    </Box>
  )
}

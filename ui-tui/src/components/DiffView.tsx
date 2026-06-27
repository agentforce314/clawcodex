/**
 * Renders a line diff (from diff.ts) the way the original Claude Code does:
 *
 *  - a NON-colored, right-aligned line-number gutter;
 *  - the code is SYNTAX-HIGHLIGHTED (via cli-highlight) on top of the add/del
 *    tint — the background is baked into the ANSI and re-applied after every
 *    syntax reset, so a `\x1b[0m` mid-line doesn't punch a hole in the tint;
 *  - the tint extends only to the LONGEST line in the hunk (capped at the
 *    terminal width), not the full terminal — short lines get a tight block,
 *    not a full-width bar;
 *  - long lines wrap (ANSI-aware) keeping the marker + tint on each row;
 *  - context lines (unchanged) render highlighted with no tint.
 *
 * Topped with an "⎿ Added N, removed M lines" summary.
 */
import { highlight } from 'cli-highlight'
import { Box, Text } from 'ink'
import React from 'react'
import wrapAnsi from 'wrap-ansi'
import { theme } from '../theme.js'
import type { DiffLine } from '../diff.js'

const MAX_LINES = 80
const RESET = '\x1b[0m'
const ADD_BG: [number, number, number] = [34, 92, 43]
const DEL_BG: [number, number, number] = [122, 41, 54]

/** Visible width (ANSI codes don't count). */
const stripAnsi = (s: string): string => s.replace(/\x1b\[[0-9;]*m/g, '')

const LANG: Record<string, string> = {
  ts: 'typescript', tsx: 'typescript', mts: 'typescript', cts: 'typescript',
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  py: 'python', rs: 'rust', go: 'go', rb: 'ruby', java: 'java', kt: 'kotlin',
  c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp', cs: 'csharp', swift: 'swift',
  php: 'php', json: 'json', yml: 'yaml', yaml: 'yaml', toml: 'ini', ini: 'ini',
  sh: 'bash', bash: 'bash', zsh: 'bash', md: 'markdown', css: 'css', scss: 'scss',
  html: 'xml', xml: 'xml', sql: 'sql', lua: 'lua', dockerfile: 'dockerfile',
}
function langOf(path?: string): string | undefined {
  const ext = (path || '').split(/[./\\]/).pop()?.toLowerCase()
  return ext ? LANG[ext] : undefined
}
function hl(code: string, lang?: string): string {
  if (!code) return ''
  try {
    return highlight(code, { language: lang, ignoreIllegals: true })
  } catch {
    return code
  }
}

/**
 * Build one rendered row: marker + highlighted code, padded to `width`, with the
 * background re-applied after every syntax reset so the tint spans the full row.
 */
function bgRow(ansi: string, bg: [number, number, number] | null, marker: string, width: number): string {
  const pad = ' '.repeat(Math.max(0, width - 1 - stripAnsi(ansi).length)) // -1 for the marker
  if (!bg) return `${marker}${ansi}${pad}`
  const BG = `\x1b[48;2;${bg[0]};${bg[1]};${bg[2]}m`
  const body = ansi.replace(/\x1b\[0m/g, RESET + BG) // keep the tint past each reset
  return `${BG}${marker}${body}${pad}${RESET}`
}

export function DiffView({ lines, filePath }: { lines: DiffLine[]; filePath?: string }): React.ReactElement {
  const shown = lines.slice(0, MAX_LINES)
  const extra = lines.length - shown.length
  const lang = langOf(filePath)
  const numW = Math.max(1, ...lines.map((l) => String(l.oldNo ?? l.newNo ?? 0).length))
  const cols = process.stdout.columns ?? 80
  const indent = 2 // sit under the "⎿" summary
  const gutterW = indent + numW + 1 // indent + right-aligned number + a space
  const contentMax = Math.max(8, cols - gutterW - 1)
  // Tint width = the longest line in the hunk (marker + code), capped at the
  // terminal — a tight block for short hunks instead of a full-width bar.
  const longest = Math.max(2, ...shown.map((l) => 1 + (l.text?.length ?? 0)))
  const blockW = Math.min(contentMax, longest)
  const segW = Math.max(4, blockW) // wrap target (marker is counted in bgRow)
  const added = lines.filter((l) => l.type === 'add').length
  const removed = lines.filter((l) => l.type === 'del').length

  return (
    <Box flexDirection="column">
      <Text color={theme.dim}>
        {`  ⎿ Added ${added} line${added === 1 ? '' : 's'}, removed ${removed} line${removed === 1 ? '' : 's'}`}
      </Text>
      {shown.map((l, i) => {
        const no = l.type === 'del' ? l.oldNo : l.newNo
        const marker = l.type === 'add' ? '+' : l.type === 'del' ? '-' : ' '
        const bg = l.type === 'add' ? ADD_BG : l.type === 'del' ? DEL_BG : null
        const ansi = hl(l.text ?? '', lang)
        const rows =
          stripAnsi(ansi).length <= segW - 1
            ? [ansi]
            : wrapAnsi(ansi, segW - 1, { hard: true, trim: false }).split('\n')
        return (
          <Box key={i}>
            <Box width={gutterW} flexShrink={0}>
              <Text color={theme.dim}>{`  ${String(no ?? '').padStart(numW)} `}</Text>
            </Box>
            <Box flexDirection="column">
              {rows.map((row, j) => (
                <Text key={j}>{bgRow(row, bg, j === 0 ? marker : ' ', blockW)}</Text>
              ))}
            </Box>
          </Box>
        )
      })}
      {extra > 0 ? <Text color={theme.dim}>{`    … +${extra} more lines`}</Text> : null}
    </Box>
  )
}

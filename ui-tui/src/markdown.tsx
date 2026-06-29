/**
 * Minimal, dependency-free Markdown → Ink renderer.
 *
 * Handles the subset assistants actually emit: fenced code blocks, ATX
 * headings, bullet/ordered lists, blockquotes, horizontal rules, and inline
 * `code` / **bold** / *italic* / [links](url). It is deliberately tolerant of
 * partial input (an unterminated ``` fence renders the remainder as code) so it
 * can render a live streaming buffer mid-token. Not a spec-complete parser —
 * a focused port of the Claude-Code look, full control, zero deps.
 */
import { highlight } from 'cli-highlight'
import { Box, Text } from 'ink'
import React from 'react'
import stringWidth from 'string-width'
import wrapAnsi from 'wrap-ansi'
import { note as perfNote } from './perfDebug.js'
import { theme } from './theme.js'

/** Syntax-highlight a code block to an ANSI string (Ink Text passes ANSI through). */
function highlightCode(code: string, lang?: string): string[] {
  try {
    perfNote('md-highlight(cli-highlight)')
    const r = highlight(code, { language: lang, ignoreIllegals: true }).split('\n')
    perfNote('md-highlight-done')
    return r
  } catch {
    return code.split('\n')
  }
}

// ── inline spans ──────────────────────────────────────────────────────────

const INLINE_RE =
  /(`[^`]+`)|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\([^)\s]+\))/

/** Parse one line of inline markdown into styled <Text> spans. */
export function parseInline(text: string): React.ReactNode[] {
  const out: React.ReactNode[] = []
  let rest = text
  // Local, deterministic keys (reset per call) so React can reconcile the
  // inline spans across re-renders of a streaming buffer.
  let n = 0
  const k = () => `s${n++}`
  while (rest.length > 0) {
    const m = INLINE_RE.exec(rest)
    if (!m || m.index === undefined) {
      out.push(<Text key={k()}>{rest}</Text>)
      break
    }
    if (m.index > 0) {
      out.push(<Text key={k()}>{rest.slice(0, m.index)}</Text>)
    }
    const tok = m[0]
    if (m[1]) {
      out.push(
        <Text key={k()} color={theme.suggestion}>
          {tok.slice(1, -1)}
        </Text>,
      )
    } else if (m[2]) {
      out.push(
        <Text key={k()} bold>
          {tok.slice(2, -2)}
        </Text>,
      )
    } else if (m[3]) {
      out.push(
        <Text key={k()} italic>
          {tok.slice(1, -1)}
        </Text>,
      )
    } else if (m[4]) {
      const close = tok.indexOf('](')
      const label = tok.slice(1, close)
      const url = tok.slice(close + 2, -1)
      out.push(
        <Text key={k()}>
          <Text color={theme.link} underline>
            {label}
          </Text>
          <Text color={theme.dim}> ({url})</Text>
        </Text>,
      )
    }
    rest = rest.slice(m.index + tok.length)
  }
  return out
}

/** Strip inline markdown markers to plain text (for table cells, where styled
 *  spans can't be cleanly wrapped). */
function stripInline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/_([^_]+)_/g, '$1')
    .replace(/\[([^\]]+)\]\([^)\s]+\)/g, '$1')
}

// ── block elements ──────────────────────────────────────────────────────────

type Align = 'left' | 'center' | 'right'
type Block =
  | { type: 'code'; lang?: string; lines: string[] }
  | { type: 'heading'; level: number; text: string }
  | { type: 'quote'; lines: string[] }
  | { type: 'list'; items: { ordered: boolean; marker: string; text: string }[] }
  | { type: 'table'; header: string[]; aligns: Align[]; rows: string[][] }
  | { type: 'hr' }
  | { type: 'para'; lines: string[] }

/** Split a `| a | b |` row into trimmed cells (tolerant of missing edge pipes). */
function parseTableRow(line: string): string[] {
  let s = line.trim()
  if (s.startsWith('|')) s = s.slice(1)
  if (s.endsWith('|')) s = s.slice(0, -1)
  return s.split('|').map((c) => c.trim())
}

/** A GFM table delimiter row, e.g. `|:---|---:|:--:|`. */
function isTableSeparator(line: string): boolean {
  return /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/.test(line) && line.includes('-')
}

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\t/g, '  ').split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i] ?? ''
    const fence = line.match(/^\s*```(.*)$/)
    if (fence) {
      const lang = fence[1]?.trim() || undefined
      const body: string[] = []
      i++
      while (i < lines.length && !/^\s*```/.test(lines[i] ?? '')) {
        body.push(lines[i] ?? '')
        i++
      }
      i++ // consume closing fence (ok if absent — partial stream)
      blocks.push({ type: 'code', lang, lines: body })
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1]!.length, text: heading[2]! })
      i++
      continue
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      blocks.push({ type: 'hr' })
      i++
      continue
    }
    if (/^\s*>/.test(line)) {
      const body: string[] = []
      while (i < lines.length && /^\s*>/.test(lines[i] ?? '')) {
        body.push((lines[i] ?? '').replace(/^\s*>\s?/, ''))
        i++
      }
      blocks.push({ type: 'quote', lines: body })
      continue
    }
    // GFM table: a header row followed by a `---|---` delimiter row.
    if (line.includes('|') && isTableSeparator(lines[i + 1] ?? '')) {
      const header = parseTableRow(line)
      const aligns: Align[] = parseTableRow(lines[i + 1] ?? '').map((c) =>
        c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : 'left',
      )
      i += 2
      const rows: string[][] = []
      while (i < lines.length && (lines[i] ?? '').includes('|') && (lines[i] ?? '').trim() !== '') {
        rows.push(parseTableRow(lines[i] ?? ''))
        i++
      }
      blocks.push({ type: 'table', header, aligns, rows })
      continue
    }
    const listItem = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/)
    if (listItem) {
      const items: { ordered: boolean; marker: string; text: string }[] = []
      while (i < lines.length) {
        const m = (lines[i] ?? '').match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/)
        if (!m) break
        const ordered = /\d/.test(m[2]!)
        items.push({ ordered, marker: m[2]!, text: m[3]! })
        i++
      }
      blocks.push({ type: 'list', items })
      continue
    }
    if (line.trim() === '') {
      i++
      continue
    }
    const body: string[] = []
    while (
      i < lines.length &&
      (lines[i] ?? '').trim() !== '' &&
      !/^\s*```/.test(lines[i] ?? '') &&
      !/^(#{1,6})\s+/.test(lines[i] ?? '') &&
      !/^\s*>/.test(lines[i] ?? '') &&
      !/^(\s*)([-*+]|\d+[.)])\s+/.test(lines[i] ?? '')
    ) {
      body.push(lines[i] ?? '')
      i++
    }
    blocks.push({ type: 'para', lines: body })
  }
  return blocks
}

const HEADING_COLOR = [theme.heading, theme.heading, theme.heading, theme.heading, theme.dim, theme.dim]

const MIN_COL = 5

/** Pad `s` to `width` per alignment (visible-width aware). */
function pad(s: string, width: number, align: Align): string {
  const gap = Math.max(0, width - stringWidth(s))
  if (align === 'right') return ' '.repeat(gap) + s
  if (align === 'center') {
    const l = Math.floor(gap / 2)
    return ' '.repeat(l) + s + ' '.repeat(gap - l)
  }
  return s + ' '.repeat(gap)
}

/** Render a GFM table with box-drawing borders, a bold header, per-column
 *  alignment, and cell wrapping — the original Claude Code table look. */
function TableBlock({ block }: { block: Extract<Block, { type: 'table' }> }): React.ReactElement {
  const numCols = block.header.length
  const rows = block.rows.map((r) => Array.from({ length: numCols }, (_, c) => stripInline(r[c] ?? '')))
  const header = Array.from({ length: numCols }, (_, c) => stripInline(block.header[c] ?? ''))
  const aligns = Array.from({ length: numCols }, (_, c) => block.aligns[c] ?? 'left')

  // Natural width per column, then shrink proportionally if the table overflows.
  const natural = Array.from({ length: numCols }, (_, c) =>
    Math.max(MIN_COL, ...[header, ...rows].map((r) => stringWidth(r[c] ?? ''))),
  )
  const overhead = numCols * 3 + 1 // "│ " + " │ "… borders + padding
  const termW = (process.stdout.columns ?? 80) - 1
  let widths = natural
  const sum = natural.reduce((a, b) => a + b, 0)
  if (sum + overhead > termW) {
    const avail = Math.max(numCols * MIN_COL, termW - overhead)
    widths = natural.map((w) => Math.max(MIN_COL, Math.floor((w / sum) * avail)))
  }

  const rule = (l: string, m: string, r: string): React.ReactElement => (
    <Text color={theme.subtle}>{l + widths.map((w) => '─'.repeat(w + 2)).join(m) + r}</Text>
  )
  const renderRow = (cells: string[], bold: boolean, key: string): React.ReactElement[] => {
    const wrapped = cells.map((c, i) =>
      wrapAnsi(c, widths[i] ?? MIN_COL, { hard: true, trim: false }).split('\n'),
    )
    const height = Math.max(1, ...wrapped.map((w) => w.length))
    const out: React.ReactElement[] = []
    for (let li = 0; li < height; li++) {
      const segs: React.ReactNode[] = [
        <Text key="l" color={theme.subtle}>
          {'│ '}
        </Text>,
      ]
      for (let c = 0; c < numCols; c++) {
        segs.push(
          <Text key={`c${c}`} bold={bold}>
            {pad(wrapped[c]?.[li] ?? '', widths[c] ?? MIN_COL, aligns[c] ?? 'left')}
          </Text>,
        )
        segs.push(
          <Text key={`s${c}`} color={theme.subtle}>
            {c < numCols - 1 ? ' │ ' : ' │'}
          </Text>,
        )
      }
      out.push(<Text key={`${key}-${li}`}>{segs}</Text>)
    }
    return out
  }

  return (
    <Box flexDirection="column">
      {rule('┌', '┬', '┐')}
      {renderRow(header, true, 'h')}
      {rule('├', '┼', '┤')}
      {rows.map((r, ri) => (
        <React.Fragment key={ri}>{renderRow(r, false, `r${ri}`)}</React.Fragment>
      ))}
      {rule('└', '┴', '┘')}
    </Box>
  )
}

export function Markdown({ text }: { text: string }): React.ReactElement {
  perfNote(`md-render(${text.length}b)`)
  const blocks = parseBlocks(text)
  return (
    <Box flexDirection="column">
      {blocks.map((b, idx) => {
        if (b.type === 'code') {
          // Borderless, indented, syntax-highlighted via cli-highlight.
          const lines = highlightCode(b.lines.join('\n'), b.lang)
          return (
            <Box key={idx} flexDirection="column" marginY={0} paddingLeft={2}>
              {(lines.length ? lines : [' ']).map((ln, j) => (
                <Text key={j}>{ln || ' '}</Text>
              ))}
            </Box>
          )
        }
        if (b.type === 'heading') {
          return (
            <Text key={idx} bold color={HEADING_COLOR[b.level - 1]}>
              {parseInline(b.text)}
            </Text>
          )
        }
        if (b.type === 'table') {
          return <TableBlock key={idx} block={b} />
        }
        if (b.type === 'hr') {
          return (
            <Text key={idx} color={theme.dim}>
              {'─'.repeat(40)}
            </Text>
          )
        }
        if (b.type === 'quote') {
          return (
            <Box key={idx} flexDirection="column">
              {b.lines.map((ln, j) => (
                <Text key={j} color={theme.dim}>
                  {'│ '}
                  {parseInline(ln)}
                </Text>
              ))}
            </Box>
          )
        }
        if (b.type === 'list') {
          return (
            <Box key={idx} flexDirection="column">
              {b.items.map((it, j) => {
                const num = parseInt(it.marker, 10)
                const bullet = it.ordered ? `${Number.isFinite(num) ? num : j + 1}. ` : '- '
                return (
                  <Box key={j}>
                    <Text color={theme.assistant}>{`  ${bullet}`}</Text>
                    <Text>{parseInline(it.text)}</Text>
                  </Box>
                )
              })}
            </Box>
          )
        }
        // paragraph
        return (
          <Box key={idx} flexDirection="column">
            {b.lines.map((ln, j) => (
              <Text key={j}>{parseInline(ln)}</Text>
            ))}
          </Box>
        )
      })}
    </Box>
  )
}

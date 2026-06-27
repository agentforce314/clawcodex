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
import { theme } from './theme.js'

/** Syntax-highlight a code block to an ANSI string (Ink Text passes ANSI through). */
function highlightCode(code: string, lang?: string): string[] {
  try {
    return highlight(code, { language: lang, ignoreIllegals: true }).split('\n')
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

// ── block elements ──────────────────────────────────────────────────────────

type Block =
  | { type: 'code'; lang?: string; lines: string[] }
  | { type: 'heading'; level: number; text: string }
  | { type: 'quote'; lines: string[] }
  | { type: 'list'; items: { ordered: boolean; marker: string; text: string }[] }
  | { type: 'hr' }
  | { type: 'para'; lines: string[] }

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

export function Markdown({ text }: { text: string }): React.ReactElement {
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

import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

// gatewayClient spawns the agent-server on import-time side effects only when
// instantiated; formatToolResult is a pure export, but mock spawn defensively.
vi.mock('node:child_process', () => ({ spawn: () => new EventEmitter() }))

// Styled-output assertions need a color-capable terminal profile — the
// renderer snapshots support at module load, so hoist the env ahead of the
// @clawcodex/ink import (PassThrough stdout has isTTY=false otherwise).
vi.hoisted(() => {
  process.env.FORCE_COLOR = '3'
  process.env.COLORTERM = 'truecolor'
  delete process.env.NO_COLOR
})

import { ToolTrail } from '../components/thinking.js'
import { formatToolResult } from '../gatewayClient.js'
import { buildToolTrailLine, stripAnsi } from '../lib/text.js'
import { estimatedMsgHeight } from '../lib/virtualHeights.js'
import { DEFAULT_THEME } from '../theme.js'

// ── formatToolResult: per-tool summaries (original CC transcript) ────────────

describe('formatToolResult', () => {
  it('summarizes Read results as a line count', () => {
    expect(formatToolResult('Read', '1\tfoo\n2\tbar\n')).toBe('Read 2 lines')
    expect(formatToolResult('Read', '1\tfoo\n')).toBe('Read 1 line')
  })

  it('summarizes Grep results as found-lines with the expand hint', () => {
    expect(formatToolResult('Grep', 'a.ts:1: x\na.ts:9: y')).toBe('Found 2 lines (ctrl+o to expand)')
    expect(formatToolResult('Grep', 'No matches found')).toBe('Found 0 lines')
  })

  it('summarizes Glob results as found-files', () => {
    expect(formatToolResult('Glob', '/ws/a.ts')).toBe('Found 1 file (ctrl+o to expand)')
  })

  it('caps Bash output at 3 lines with an overflow hint', () => {
    const out = formatToolResult('Bash', 'l1\nl2\nl3\nl4\nl5')

    expect(out).toBe('l1\nl2\nl3\n… +2 lines (ctrl+o to expand)')
  })

  it('shows the 4th Bash line instead of a one-line hint (CC parity)', () => {
    expect(formatToolResult('Bash', 'l1\nl2\nl3\nl4')).toBe('l1\nl2\nl3\nl4')
  })

  it('renders empty Bash output as (No output)', () => {
    expect(formatToolResult('Bash', '   \n')).toBe('(No output)')
  })

  // Original CC (WebSearchTool/UI.tsx renderToolResultMessage) renders the
  // whole result as ONE line — never the snippet blob.
  it('summarizes WebSearch results as the original one-liner', () => {
    const blob =
      'Web search results for query: "obama news"\n\n' +
      '**Title A** -- long snippet A (https://a.example)\n' +
      '**Title B** -- long snippet B (https://b.example)\n\n' +
      'Links: [{"title": "Title A", "url": "https://a.example"}]\n\n' +
      'REMINDER: You MUST include the sources above in your response to the user using markdown hyperlinks.'

    // Envelope present (agent-server tool_use_result): exact CC string.
    expect(formatToolResult('WebSearch', blob, false, { durationSeconds: 24.4, searchCount: 1 })).toBe(
      'Did 1 search in 24s'
    )
    expect(formatToolResult('WebSearch', blob, false, { durationSeconds: 0.532, searchCount: 2 })).toBe(
      'Did 2 searches in 532ms'
    )

    // No envelope (older backend): count recovered from the blob, no time.
    expect(formatToolResult('WebSearch', blob)).toBe('Did 1 search')
    expect(formatToolResult('WebSearch', 'Web search results for query: "x"\n\nNo results found.')).toBe(
      'Did 0 searches'
    )

    // Envelope without a matching stored tool name (mid-turn attach).
    expect(formatToolResult(undefined, blob, false, { durationSeconds: 2, searchCount: 1 })).toBe(
      'Did 1 search in 2s'
    )

    // Errors keep the error path — never a fake "Did N searches".
    expect(formatToolResult('WebSearch', 'rate limited', true)).toBe('Error: rate limited')
  })

  it('prefixes errors and caps them at 10 lines', () => {
    expect(formatToolResult('Bash', 'boom', true)).toBe('Error: boom')
    expect(formatToolResult('Bash', 'Error: already prefixed', true)).toBe('Error: already prefixed')

    const big = Array.from({ length: 14 }, (_, i) => `e${i}`).join('\n')
    const out = formatToolResult('Bash', big, true)
    const lines = out.split('\n')

    expect(lines).toHaveLength(11)
    expect(lines[0]).toBe('Error: e0')
    expect(lines[10]).toBe('… +4 lines (ctrl+o to see all)')
  })
})

// ── virtualHeights: multi-line tool entries count rendered rows ──────────────

describe('estimatedMsgHeight with multi-line tool details', () => {
  it('counts one row per rendered detail line, not per entry', () => {
    const base = {
      kind: 'trail' as const,
      role: 'system' as const,
      text: '',
      tools: [buildToolTrailLine('Bash', 'ls', false, 'a\nb\nc')]
    }

    const single = {
      ...base,
      tools: [buildToolTrailLine('Bash', 'ls', false, 'a')]
    }

    const opts = { compact: false, details: true, leadGap: false, withSeparator: false }
    const tall = estimatedMsgHeight(base, 80, opts)
    const short = estimatedMsgHeight(single, 80, opts)

    expect(tall - short).toBe(2) // two extra detail rows
  })
})

// ── ToolTrail render: CC anatomy ─────────────────────────────────────────────

const renderToString = (element: React.ReactElement): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 100, isTTY: false, rows: 40 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  const instance = renderSync(element, {
    patchConsole: false,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  instance.unmount()
  instance.cleanup()

  return output
}

describe('ToolTrail rendering', () => {
  it('renders a 3-line Bash result as three aligned rows under one connector', () => {
    const line = buildToolTrailLine('Bash', 'ls src/', false, 'components\nlib\napp')

    const output = renderToString(
      React.createElement(ToolTrail, { detailsMode: 'expanded', t: DEFAULT_THEME, trail: [line] })
    )

    const rows = stripAnsi(output)
      .split('\n')
      .filter(r => r.trim().length > 0)

    const bullet = rows.findIndex(r => r.includes('Bash(ls src/)'))

    expect(bullet).toBeGreaterThanOrEqual(0)
    expect(rows[bullet]).not.toMatch(/\(\d+(\.\d+)?s\)/) // no duration
    expect(rows[bullet + 1]).toMatch(/⎿\s+components/)
    // continuations align under the content column, no second connector
    expect(rows[bullet + 2]).toMatch(/^\s{5,}lib/)
    expect(rows[bullet + 2]).not.toContain('⎿')
    expect(rows[bullet + 3]).toMatch(/^\s{5,}app/)
  })

  it('bolds the tool name and keeps args plain', () => {
    const line = buildToolTrailLine('Bash', 'npm test', false, 'ok')

    const output = renderToString(
      React.createElement(ToolTrail, { detailsMode: 'expanded', t: DEFAULT_THEME, trail: [line] })
    )

    // bold SGR immediately around the name, not the args
    // eslint-disable-next-line no-control-regex
    expect(output).toMatch(/\x1b\[1m[^\x1b]*Bash/)
    expect(stripAnsi(output)).toContain('Bash(npm test)')
  })

  it('marks failed tools with an error bullet while the name row stays plain', () => {
    const line = buildToolTrailLine('Bash', 'false', true, 'Error: exit 1')

    const output = renderToString(
      React.createElement(ToolTrail, { detailsMode: 'expanded', t: DEFAULT_THEME, trail: [line] })
    )

    const plain = stripAnsi(output)

    expect(plain).toContain('Error: exit 1')
    // error red on the bullet: theme error #FF6B80 → 255;107;128
    expect(output).toContain('255;107;128')
  })
})

import { PassThrough } from 'stream'

import { Box, renderSync, ScrollBox, stringWidth } from '@clawcodex/ink'
import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import { SessionPanel } from '../components/branding.js'
import {
  TRANSCRIPT_PADDING_X,
  TRANSCRIPT_SCROLLBAR_GUTTER,
  transcriptPanelWidth
} from '../lib/inputMetrics.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'
import type { SessionInfo, Theme } from '../types.js'

// Render-level invariant: every row the header box emits is exactly `cols`
// wide, with its border glyphs intact, at every width and for every cwd.
//
// This is the test that actually holds the layout. The unit tests in
// brandingLayout.test.ts pin the arithmetic, but the arithmetic is only correct
// relative to the JSX chrome (border 1+1, paddingX 1+1, divider marginX 1+1) —
// change `marginX={1}` to `2` and those still pass while the box overflows.
//
// It also pins the border-title guard: ink stops embedding `borderText` once
// the title reaches `cols - 2` and instead returns the raw title with the
// corners dropped (render-border.ts:43-44), which blanks the top row entirely.
//
// NOTE: SessionPanel reads its width from useStdout(), which is hardcoded to
// `process.stdout` — NOT the stream passed to renderSync. Assigning `columns`
// to the PassThrough (the pattern in brandingMcpCount.test.ts) leaves the
// component on its `?? 100` fallback, so every "render at width N" silently
// produces the same 100-column layout clipped to N. It looks responsive and
// tests nothing. Pin process.stdout.columns instead.

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

const originalColumns = Object.getOwnPropertyDescriptor(process.stdout, 'columns')

afterEach(() => {
  if (originalColumns) {
    Object.defineProperty(process.stdout, 'columns', originalColumns)

    return
  }

  // When stdout is piped — CI, and any plain `npm test` — process.stdout has no
  // OWN `columns` descriptor, so the restore branch above never fires and the
  // property this file defines stays pinned at the last width used for the rest
  // of the vitest worker, which is reused across files. Delete it instead.
  delete (process.stdout as { columns?: number }).columns
})

const info = (over: Partial<SessionInfo> = {}): SessionInfo => ({
  cwd: '/Users/ericlee2/workspace/clawcodex',
  mcp_servers: [{ connected: true, name: 'nous-support', status: 'connected', tools: 6, transport: 'http' }],
  model: 'anthropic/claude-opus-5',
  profile_name: 'Claude Max',
  skills: { core: ['a', 'b'] },
  system_prompt: 'You are clawcodex.',
  tools: { file_tools: ['Read', 'Write', 'Edit'], shell_tools: ['Bash'] },
  version: '1.0.0',
  ...over
})

/** `oversizedContainer` drops the scrollbar gutter, reproducing the first paint
 *  where the ScrollBox has not yet reserved it. */
async function boxRows(
  cols: number,
  sessionInfo: SessionInfo,
  theme: Theme = DEFAULT_THEME,
  { oversizedContainer = false }: { oversizedContainer?: boolean } = {}
): Promise<string[]> {
  const maxWidth = transcriptPanelWidth(cols)
  const gutter = oversizedContainer ? 0 : TRANSCRIPT_SCROLLBAR_GUTTER
  Object.defineProperty(process.stdout, 'columns', { configurable: true, value: cols, writable: true })

  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()

  Object.assign(stdout, { columns: cols, isTTY: false, rows: 60 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })

  let captured = ''
  stdout.on('data', chunk => {
    captured += chunk.toString()
  })

  // Mirrors appLayout's actual wrapper, NOT the root and NOT a plain Box.
  //
  // Both details are load-bearing. Rendering at the root is what let a
  // two-column overflow pass this file originally. And a plain <Box> stand-in
  // is not equivalent either: it has flexShrink, so an over-wide child is
  // quietly shrunk to fit and the bug disappears. ScrollBox constrains its
  // children instead of being expanded by them, so an over-wide child is
  // CLIPPED — which is why the right border vanished on screen but not in a
  // Box-wrapped test.
  const instance = renderSync(
    React.createElement(
      Box,
      { flexDirection: 'row', height: 40, width: cols },
      React.createElement(
        ScrollBox,
        { flexDirection: 'column', flexGrow: 1, flexShrink: 1 },
        React.createElement(
          Box,
          { flexDirection: 'column', paddingX: TRANSCRIPT_PADDING_X / 2 },
          React.createElement(SessionPanel, { info: sessionInfo, maxWidth, sid: 'a1b2c3d4', t: theme })
        )
      ),
      gutter ? React.createElement(Box, { flexShrink: 0, marginLeft: gutter - 1, width: gutter - 1 }) : null
    ),
    {
      patchConsole: false,
      stderr: stderr as NodeJS.WriteStream,
      stdin: stdin as NodeJS.ReadStream,
      stdout: stdout as NodeJS.WriteStream
    }
  )

  try {
    await delay(30)

    // stripAnsi, not a hand-rolled /\[[0-9;]*m/ — that leaves the ESC byte, so
    // rows still start with an escape and the `^[╭│╰]` filter below silently
    // drops them. A vacuous `rows.find(...)` then makes the corner assertions
    // pass against `undefined`.
    const plain = stripAnsi(captured)

    // Only the box rows; the panel also emits a trailing margin line. The
    // wrapper's left padding means rows no longer start at column 0.
    return plain
      .split('\n')
      .map(line => line.trimEnd())
      .filter(line => /[╭│╰]/.test(line))
      .map(line => line.slice(line.search(/[╭│╰]/)))
  } finally {
    instance.unmount()
    instance.cleanup()
  }
}

const WIDTHS = [34, 40, 60, 72, 88, 96, 100, 140, 200]

describe('header box render width', () => {
  it.each(WIDTHS)('fills its container exactly at %i terminal columns', async cols => {
    const rows = await boxRows(cols, info())

    expect(rows.length).toBeGreaterThan(0)

    // The container, not the terminal: overflowing it clips the right border.
    for (const row of rows) {
      expect(stringWidth(row)).toBe(transcriptPanelWidth(cols))
    }
  })

  it.each(WIDTHS)('keeps the border corners and edges intact at %i columns', async cols => {
    const rows = await boxRows(cols, info())
    const top = rows.find(r => r.startsWith('╭'))
    const bottom = rows.find(r => r.startsWith('╰'))

    // Non-vacuity: if the rows were filtered away these assertions would
    // otherwise "pass" against undefined.
    expect(top).toBeDefined()
    expect(bottom).toBeDefined()

    // The border-title collapse manifests exactly here: no corners on the top row.
    expect(top?.endsWith('╮')).toBe(true)
    expect(bottom?.endsWith('╯')).toBe(true)

    for (const row of rows.filter(r => r.startsWith('│'))) {
      expect(row.endsWith('│')).toBe(true)
    }
  })

  // The regression that made the border invisible in a real terminal.
  //
  // On the first paint the transcript's ScrollBox has not settled and reports
  // its full width, WITHOUT the scrollbar gutter its steady-state layout
  // reserves. A box that sizes to its container therefore renders two columns
  // too wide, gets clipped, and — because the intro row is committed to
  // scrollback and never repainted — stays broken until a resize.
  //
  // So the panel must hold its own width against a container that is bigger
  // than the steady-state one. `oversizedContainer` reproduces frame 1 by
  // omitting the gutter.
  it.each(WIDTHS)('holds its width when the container is mis-measured at %i columns', async cols => {
    const rows = await boxRows(cols, info(), DEFAULT_THEME, { oversizedContainer: true })
    const top = rows.find(r => r.startsWith('╭'))
    const bottom = rows.find(r => r.startsWith('╰'))

    expect(rows.length).toBeGreaterThan(0)
    expect(top).toBeDefined()
    expect(bottom).toBeDefined()
    expect(top?.endsWith('╮')).toBe(true)
    expect(bottom?.endsWith('╯')).toBe(true)

    // Still the steady-state width, not the container's inflated one.
    for (const row of rows) {
      expect(stringWidth(row)).toBe(transcriptPanelWidth(cols))
    }
  })

  // A branded install pushes the title past `cols - 2` at ordinary widths.
  it('degrades a long brand title instead of destroying the top border', async () => {
    const branded: Theme = { ...DEFAULT_THEME, brand: { ...DEFAULT_THEME.brand, name: 'Acme Engineering Assistant' } }

    for (const cols of [30, 36, 40, 60]) {
      const rows = await boxRows(cols, info(), branded)
      const top = rows.find(r => r.startsWith('╭'))

      expect(top).toBeDefined()
      expect(stringWidth(top ?? '')).toBe(transcriptPanelWidth(cols))
      expect(top?.endsWith('╮')).toBe(true)
    }
  })

  // The clip-by-code-unit bug showed up only here: an over-wide truncated path
  // re-truncates at the far end, eliding both ends and losing the tail segment.
  it('keeps rows exact for a CJK cwd', async () => {
    const rows = await boxRows(100, info({ cwd: `/Users/me/${'目'.repeat(30)}/项目` }))

    // Non-vacuity: a `for…of` over an empty array asserts nothing, and these two
    // are the tests guarding the width-aware clip.
    expect(rows.length).toBeGreaterThan(0)

    for (const row of rows) {
      expect(stringWidth(row)).toBe(transcriptPanelWidth(100))
    }
  })

  it('keeps rows exact for an emoji cwd', async () => {
    const rows = await boxRows(100, info({ cwd: `/Users/me/${'🎉'.repeat(20)}/folder` }))

    expect(rows.length).toBeGreaterThan(0)

    for (const row of rows) {
      expect(stringWidth(row)).toBe(transcriptPanelWidth(100))
    }
  })

  // Keycap sequences undercount per code point (digit + VS16 + U+20E3 sums to 1
  // column, the cluster is 2), and regional-indicator flags split into reversed
  // letter pairs. Both are grapheme-cluster failures, not code-point ones.
  it('keeps rows exact for keycap and flag cwds', async () => {
    for (const dir of ['1️⃣'.repeat(12), '🇸🇪'.repeat(10)]) {
      const rows = await boxRows(100, info({ cwd: `/Users/me/${dir}` }))

      expect(rows.length).toBeGreaterThan(0)

      for (const row of rows) {
        expect(stringWidth(row)).toBe(transcriptPanelWidth(100))
      }
    }
  })
})

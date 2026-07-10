import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Fake the agent-server child so tests can feed NDJSON protocol lines and
// observe the GatewayEvents (same harness as gatewayClient.test.ts).
const harness = vi.hoisted(() => ({ proc: null as null | EventEmitter, spawnCalls: [] as unknown[][] }))

vi.mock('node:child_process', () => ({
  spawn: (...args: unknown[]) => {
    harness.spawnCalls.push(args)

    return harness.proc
  }
}))

import { renderSync } from '@clawcodex/ink'
import React from 'react'

import { turnController } from '../app/turnController.js'
import { getTurnState, resetTurnState } from '../app/turnStore.js'
import { DiffView } from '../components/diffView.js'
import { MessageLine } from '../components/messageLine.js'
import { GatewayClient } from '../gatewayClient.js'
import { ColorDiff, ensureHighlighter } from '../lib/colorDiff.js'
import { buildToolTrailLine, stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'
import type { MsgDiffData } from '../types.js'

class FakeProc extends EventEmitter {
  kill = vi.fn()
  stderr = new PassThrough()
  stdin = new PassThrough()
  stdout = new PassThrough()

  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

const HUNK = {
  lines: [' context line', '-  id: "obama-as-author",', '+  id: "drone-warfare",', ' tail line'],
  newLines: 3,
  newStart: 220,
  oldLines: 3,
  oldStart: 220
}

const TOOL_USE_RESULT = {
  filePath: '/ws/src/posts.ts',
  firstLine: 'export const posts = [',
  structuredPatch: [
    {
      lines: HUNK.lines,
      newLines: HUNK.newLines,
      newStart: HUNK.newStart,
      oldLines: HUNK.oldLines,
      oldStart: HUNK.oldStart
    }
  ],
  type: 'update'
}

// ── ColorDiff layout (the faithful renderer itself) ─────────────────────────

describe('ColorDiff layout', () => {
  const prevColorTerm = process.env.COLORTERM

  beforeEach(() => {
    process.env.COLORTERM = 'truecolor'
  })

  afterEach(() => {
    if (prevColorTerm === undefined) {
      delete process.env.COLORTERM
    } else {
      process.env.COLORTERM = prevColorTerm
    }
  })

  it('renders the original gutter: right-aligned line number, marker, padded background rows', async () => {
    await ensureHighlighter()
    const rows = new ColorDiff(HUNK, null, '/ws/src/posts.ts', null).render('dark', 60, false)!
    const plain = rows.map(row => stripAnsi(row))

    // ` NNN ` gutter + marker + content; context rows unpadded, changed rows
    // padded to the wrap width (60 − 3 digits − 3 = 54 content columns).
    expect(plain[0]).toBe(' 220  context line')
    expect(plain[1]).toBe(' 221 -  id: "obama-as-author",' + ' '.repeat(54 - '  id: "obama-as-author",'.length))
    expect(plain[2]).toBe(' 221 +  id: "drone-warfare",' + ' '.repeat(54 - '  id: "drone-warfare",'.length))
    expect(plain[3]).toBe(' 222  tail line')
    // no ---/+++ headers, ever
    expect(plain.join('\n')).not.toMatch(/^[-+]{3} /m)
  })

  it('paints the original dark-theme backgrounds and word-diff highlights', () => {
    // Small change (one token) so the 0.4 change-ratio threshold admits
    // word-level highlighting.
    const small = {
      lines: [' before', '-  date: "2026-06-21",', '+  date: "2026-06-22",', ' after'],
      newLines: 3,
      newStart: 10,
      oldLines: 3,
      oldStart: 10
    }

    const rows = new ColorDiff(small, null, '/ws/src/posts.ts', null).render('dark', 60, false)!

    // deleteLine rgb(61,1,0) on the removed row, deleteWord rgb(92,2,0) on
    // the changed token; addLine rgb(2,40,0) / addWord rgb(4,71,0).
    expect(rows[1]).toContain('48;2;61;1;0')
    expect(rows[1]).toContain('48;2;92;2;0')
    expect(rows[2]).toContain('48;2;2;40;0')
    expect(rows[2]).toContain('48;2;4;71;0')
    // context rows keep the terminal default background
    expect(rows[0]).not.toContain('48;2;')
  })

  it('rejects word-diff when most of the line changed (threshold parity)', () => {
    const rows = new ColorDiff(HUNK, null, '/ws/src/posts.ts', null).render('dark', 60, false)!

    expect(rows[1]).toContain('48;2;61;1;0')
    expect(rows[1]).not.toContain('48;2;92;2;0')
  })

  it('wraps long lines with a blank continuation gutter', () => {
    const wide = {
      lines: ['+' + 'x'.repeat(60)],
      newLines: 1,
      newStart: 7,
      oldLines: 0,
      oldStart: 7
    }

    const rows = new ColorDiff(wide, null, '/ws/a.txt', null).render('dark', 40, false)!
    const plain = rows.map(row => stripAnsi(row))

    expect(plain.length).toBeGreaterThan(1)
    expect(plain[0]!.startsWith(' 7 +')).toBe(true)
    expect(plain[1]!.startsWith('   +')).toBe(true)
  })
})

// ── GatewayClient: tool_use_result → structured_diff ────────────────────────

describe('GatewayClient structured diff mapping', () => {
  const prevWs = process.env.CLAWCODEX_WORKSPACE
  let events: any[]
  let gw: GatewayClient
  let proc: FakeProc

  beforeEach(() => {
    process.env.CLAWCODEX_WORKSPACE = '/ws'
    proc = new FakeProc()
    harness.proc = proc
    harness.spawnCalls = []
    events = []
    gw = new GatewayClient()
    gw.on('event', (e: any) => events.push(e))
    gw.start()
    gw.drain()
  })

  afterEach(() => {
    gw.kill()

    if (prevWs === undefined) {
      delete process.env.CLAWCODEX_WORKSPACE
    } else {
      process.env.CLAWCODEX_WORKSPACE = prevWs
    }
  })

  const last = (t: string) => [...events].reverse().find(e => e.type === t)

  const completeTool = async (id: string, name: string, input: unknown, resultMsg: Record<string, unknown>) => {
    proc.line({ message: { content: [{ id, input, name, type: 'tool_use' }] }, type: 'assistant' })
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    proc.line(resultMsg)
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())

    return last('tool.complete').payload
  }

  const userResult = (id: string, content: unknown, extra: Record<string, unknown> = {}, isError = false) => ({
    message: { content: [{ content, is_error: isError, tool_use_id: id, type: 'tool_result' }] },
    type: 'user',
    ...extra
  })

  it('maps an update tool_use_result to structured_diff and skips the legacy fake diff', async () => {
    const payload = await completeTool(
      't1',
      'Edit',
      { file_path: '/ws/src/posts.ts', new_string: 'b', old_string: 'a' },
      userResult('t1', 'ok', { tool_use_result: TOOL_USE_RESULT })
    )

    expect(payload.structured_diff).toMatchObject({
      filePath: '/ws/src/posts.ts',
      firstLine: 'export const posts = [',
      kind: 'update'
    })
    expect(payload.structured_diff.hunks).toHaveLength(1)
    expect(payload.structured_diff.hunks[0].lines).toEqual(HUNK.lines)
    expect(payload.inline_diff).toBeUndefined()
  })

  it('maps a create tool_use_result with its content preview', async () => {
    const payload = await completeTool(
      't1',
      'Write',
      { content: 'hello\nworld\n', file_path: '/ws/new.txt' },
      userResult('t1', 'ok', {
        tool_use_result: { content: 'hello\nworld\n', filePath: '/ws/new.txt', structuredPatch: [], type: 'create' }
      })
    )

    expect(payload.structured_diff).toMatchObject({ content: 'hello\nworld\n', kind: 'create' })
  })

  it('falls back to the legacy input diff when tool_use_result is absent', async () => {
    const payload = await completeTool(
      't1',
      'Edit',
      { file_path: '/ws/src/posts.ts', new_string: 'b', old_string: 'a' },
      userResult('t1', 'ok')
    )

    expect(payload.structured_diff).toBeUndefined()
    expect(payload.inline_diff).toContain('-a')
    expect(payload.inline_diff).toContain('+b')
  })

  it('rejects malformed tool_use_result shapes', async () => {
    const payload = await completeTool(
      't1',
      'Edit',
      { file_path: '/ws/x.ts', new_string: 'b', old_string: 'a' },
      userResult('t1', 'ok', { tool_use_result: { structuredPatch: [{ nope: true }], type: 'update' } })
    )

    expect(payload.structured_diff).toBeUndefined()
  })

  it('renders no diff of any kind for a failed edit', async () => {
    const payload = await completeTool(
      't1',
      'Edit',
      { file_path: '/ws/x.ts', new_string: 'b', old_string: 'a' },
      userResult('t1', 'Error: old_string not found in file', { tool_use_result: TOOL_USE_RESULT }, true)
    )

    expect(payload.structured_diff).toBeUndefined()
    expect(payload.inline_diff).toBeUndefined()
  })
})

// ── turnController: structured diff segments ────────────────────────────────

describe('turnController structured diff segments', () => {
  beforeEach(() => {
    turnController.recordError() // clears any segment state left by other suites
    resetTurnState()
    turnController.startMessage()
  })

  afterEach(() => {
    turnController.recordError()
    resetTurnState()
  })

  const diff = (): Parameters<typeof turnController.pushStructuredDiffSegment>[0] => ({
    filePath: '/ws/src/posts.ts',
    firstLine: 'export const posts = [',
    hunks: [{ ...HUNK, lines: [...HUNK.lines] }],
    kind: 'update'
  })

  it('pushes a diff segment carrying diffData and a derived ```diff fence', () => {
    turnController.pushStructuredDiffSegment(diff(), ['Edit(src/posts.ts)'])

    const segments = getTurnState().streamSegments
    expect(segments).toHaveLength(1)
    expect(segments[0]).toMatchObject({ kind: 'diff', role: 'assistant' })
    expect(segments[0]!.diffData?.hunks).toHaveLength(1)
    expect(segments[0]!.text).toBe('```diff\n' + HUNK.lines.join('\n') + '\n```')
    expect(segments[0]!.tools).toEqual(['Edit(src/posts.ts)'])
  })

  it('drops consecutive duplicate segments', () => {
    turnController.pushStructuredDiffSegment(diff())
    turnController.pushStructuredDiffSegment(diff())

    expect(getTurnState().streamSegments).toHaveLength(1)
  })

  it('caps giant patches at ingestion and records the dropped count', () => {
    const big = {
      filePath: '/ws/big.ts',
      hunks: [
        {
          lines: Array.from({ length: 300 }, (_, i) => `+line ${i}`),
          newLines: 300,
          newStart: 1,
          oldLines: 0,
          oldStart: 1
        }
      ],
      kind: 'update' as const
    }

    turnController.pushStructuredDiffSegment(big)

    const seg = getTurnState().streamSegments[0]!
    expect(seg.diffData?.hunks[0]!.lines).toHaveLength(240)
    expect(seg.diffData?.truncatedLines).toBe(60)
    expect(seg.text.split('\n')).toHaveLength(242) // fence open + 240 + fence close
  })

  it('previews create-type segments from content', () => {
    turnController.pushStructuredDiffSegment({
      content: Array.from({ length: 14 }, (_, i) => `line ${i}`).join('\n') + '\n',
      filePath: '/ws/new.txt',
      hunks: [],
      kind: 'create'
    })

    const seg = getTurnState().streamSegments[0]!
    expect(seg.diffData?.kind).toBe('create')
    // fence carries only the 10-line preview
    expect(seg.text.split('\n')).toHaveLength(12)
    expect(seg.text).toContain('+line 0')
  })

  it('renders create-type WITH hunks as the content preview, not diff rows', () => {
    // Write of a new file: difflib emits an all-additions hunk, but the
    // original renders the created-file preview (kind switch, not hunk
    // presence).
    turnController.pushStructuredDiffSegment({
      content: 'alpha\nbeta\n',
      filePath: '/ws/new.ts',
      hunks: [{ lines: ['+alpha', '+beta'], newLines: 2, newStart: 1, oldLines: 0, oldStart: 0 }],
      kind: 'create'
    })

    const seg = getTurnState().streamSegments[0]!
    expect(seg.diffData?.kind).toBe('create')
    expect(seg.text).toBe('```diff\n+alpha\n+beta\n```')
  })

  it('keeps the tool trail line when nothing is renderable (no-op update)', () => {
    turnController.recordStructuredDiffToolComplete({ filePath: '/ws/x.ts', hunks: [], kind: 'update' }, 't9', 'Write', undefined, 0.1)

    // No diff segment to render — the completion survives on the pending
    // trail (merged into the transcript at message.complete, same as the
    // plain recordToolComplete path).
    expect(getTurnState().streamSegments).toHaveLength(0)
    expect(getTurnState().streamPendingTools[0]).toContain('Write')
  })
})

// ── DiffView (MessageLine integration) ──────────────────────────────────────

describe('DiffView rendering', () => {
  const prevColorTerm = process.env.COLORTERM

  beforeEach(() => {
    process.env.COLORTERM = 'truecolor'
    delete process.env.NO_COLOR
  })

  afterEach(() => {
    if (prevColorTerm === undefined) {
      delete process.env.COLORTERM
    } else {
      process.env.COLORTERM = prevColorTerm
    }
  })

  const renderToString = (element: React.ReactElement): string => {
    const stdout = new PassThrough()
    const stdin = new PassThrough()
    const stderr = new PassThrough()
    let output = ''

    Object.assign(stdout, { columns: 80, isTTY: false, rows: 24 })
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

  const DIFF: MsgDiffData = {
    filePath: '/ws/src/posts.ts',
    firstLine: 'export const posts = [',
    hunks: [HUNK],
    kind: 'update'
  }

  it('renders the summary, gutter line numbers and colored rows', async () => {
    await ensureHighlighter()

    const output = renderToString(React.createElement(DiffView, { cols: 80, diff: DIFF, t: DEFAULT_THEME }))
    const plain = stripAnsi(output)

    expect(plain).toContain('⎿')
    expect(plain).toContain('Added 1 line, removed 1 line')
    expect(plain).toMatch(/221 -\s+id: "obama-as-author",/)
    expect(plain).toMatch(/221 \+\s+id: "drone-warfare",/)
    expect(plain).not.toContain('---')
    // real ColorDiff output, not the fallback: add/remove backgrounds present
    expect(output).toContain('48;2;61;1;0')
    expect(output).toContain('48;2;2;40;0')
  })

  it('renders a created file as "Wrote N lines" with a highlighted preview', () => {
    const output = renderToString(
      React.createElement(DiffView, {
        cols: 80,
        diff: {
          content: Array.from({ length: 14 }, (_, i) => `line ${i}`).join('\n') + '\n',
          filePath: '/ws/new.txt',
          hunks: [],
          kind: 'create'
        },
        t: DEFAULT_THEME
      })
    )

    const plain = stripAnsi(output)

    expect(plain).toContain('Wrote 14 lines to')
    expect(plain).toContain('… +4 lines')
    expect(plain).toContain(' 1 line 0')
  })

  it('renders create-with-hunks as the Wrote-N-lines preview, never diff rows', () => {
    const output = renderToString(
      React.createElement(DiffView, {
        cols: 80,
        diff: {
          content: 'alpha\nbeta\n',
          filePath: '/ws/new.ts',
          hunks: [{ lines: ['+alpha', '+beta'], newLines: 2, newStart: 1, oldLines: 0, oldStart: 0 }],
          kind: 'create'
        },
        t: DEFAULT_THEME
      })
    )
    const plain = stripAnsi(output)

    expect(plain).toContain('Wrote 2 lines to')
    expect(plain).not.toContain('Added')
    // preview rows are line-numbered content, not +-prefixed diff rows
    expect(plain).toMatch(/1 alpha/)
    expect(plain).not.toMatch(/\+alpha/)
  })

  it('renders diff segments through MessageLine without a Response separator', () => {
    const output = renderToString(
      React.createElement(MessageLine, {
        cols: 80,
        msg: {
          diffData: DIFF,
          kind: 'diff',
          role: 'assistant',
          text: '```diff\n' + HUNK.lines.join('\n') + '\n```',
          tools: [buildToolTrailLine('Edit', 'src/posts.ts', false, 'ok', 0.2)]
        },
        t: DEFAULT_THEME
      })
    )

    const plain = stripAnsi(output)

    expect(plain).toContain('Edit(src/posts.ts)')
    expect(plain).not.toContain('Response')
    expect(plain).toMatch(/221 \+\s+id: "drone-warfare",/)
  })
})

describe('structuredDiffSupported no-color gating', () => {
  // ColorDiff emits raw SGR itself (not through chalk), so the structured
  // path must bow out on the SAME signals that turn the rest of the UI
  // monochrome — otherwise a FORCE_COLOR=0 / TERM=dumb session gets colored
  // diff blocks inside an otherwise colorless transcript.
  const importFresh = async () => (await import('../components/diffView.js')).structuredDiffSupported

  // Piped test stdout has no hasColors at all; install/remove an own prop.
  const setHasColors = (v: boolean | undefined) => {
    if (v === undefined) {
      delete (process.stdout as { hasColors?: unknown }).hasColors
    } else {
      Object.defineProperty(process.stdout, 'hasColors', { configurable: true, value: () => v, writable: true })
    }
  }

  afterEach(() => {
    setHasColors(undefined)
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('falls back when the stream reports no color support (FORCE_COLOR=0 / TERM=dumb)', async () => {
    vi.resetModules()
    setHasColors(false)

    expect((await importFresh())()).toBe(false)
  })

  it('falls back under NO_COLOR even when the stream reports color', async () => {
    vi.resetModules()
    setHasColors(true)
    vi.stubEnv('NO_COLOR', '1')

    expect((await importFresh())()).toBe(false)
  })

  it('renders structured diffs on color-capable terminals', async () => {
    vi.resetModules()
    setHasColors(true)
    delete process.env.NO_COLOR

    expect((await importFresh())()).toBe(true)
  })
})

import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

const harness = vi.hoisted(() => {
  process.env.FORCE_COLOR = '3'
  process.env.COLORTERM = 'truecolor'
  delete process.env.NO_COLOR

  return { proc: null as null | EventEmitter }
})

vi.mock('node:child_process', () => ({ spawn: () => harness.proc }))

import { turnController } from '../app/turnController.js'
import { resetTurnState } from '../app/turnStore.js'
import { ToolTrail } from '../components/thinking.js'
import { GatewayClient } from '../gatewayClient.js'
import { mergeToolShelfInto } from '../lib/liveProgress.js'
import { stripAnsi } from '../lib/text.js'
import { estimatedMsgHeight } from '../lib/virtualHeights.js'
import { DEFAULT_THEME } from '../theme.js'
import type { Msg } from '../types.js'

class FakeProc extends EventEmitter {
  kill = vi.fn()
  stderr = new PassThrough()
  stdin = new PassThrough()
  stdout = new PassThrough()

  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

const renderToString = (element: React.ReactElement): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 100, isTTY: false, rows: 44 })
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

// ── gatewayClient: raw retention ─────────────────────────────────────────────

describe('GatewayClient raw result retention', () => {
  const run = async (name: string, input: unknown, result: string, isError = false) => {
    const proc = new FakeProc()
    harness.proc = proc
    const events: any[] = []
    const gw = new GatewayClient()

    gw.on('event', (e: any) => events.push(e))
    gw.start()
    gw.drain()

    const last = (t: string) => [...events].reverse().find(e => e.type === t)

    proc.line({ message: { content: [{ id: 't1', input, name, type: 'tool_use' }] }, type: 'assistant' })
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    proc.line({
      message: { content: [{ content: result, is_error: isError, tool_use_id: 't1', type: 'tool_result' }] },
      type: 'user'
    })
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())
    gw.kill()

    return last('tool.complete').payload
  }

  it('retains the full output when the summary lost information', async () => {
    const p = await run('Bash', { command: 'seq 6' }, '1\n2\n3\n4\n5\n6')

    expect(p.result_text).toBe('1\n2\n3\n… +3 lines (ctrl+o to expand)')
    expect(p.result_raw).toBe('1\n2\n3\n4\n5\n6')
  })

  it('omits result_raw when the compact form is already complete', async () => {
    const p = await run('Bash', { command: 'echo hi' }, 'hi\n')

    expect(p.result_text).toBe('hi')
    expect(p.result_raw).toBeUndefined()
  })

  it('caps retained raw output at the memory bound', async () => {
    const big = Array.from({ length: 5000 }, (_, i) => `line ${i} xxxxxxxxxx`).join('\n')
    const p = await run('Bash', { command: 'dump' }, big)

    expect(p.result_raw.length).toBeLessThanOrEqual(48_002)
    expect(p.result_raw.endsWith('…')).toBe(true)
  })

  it('retains nothing for Read (no hint, no expansion — file is in context)', async () => {
    const numbered = Array.from({ length: 40 }, (_, i) => `${i + 1}\tline`).join('\n')
    const p = await run('Read', { file_path: '/ws/x.ts' }, numbered)

    expect(p.result_text).toBe('Read 40 lines')
    expect(p.result_text).not.toContain('ctrl+o')
    expect(p.result_raw).toBeUndefined()
  })
})

// ── turnController: verbose siblings lockstep ────────────────────────────────

describe('verbose sibling pipeline', () => {
  it('builds the Args/Result verbose sibling and keeps lockstep through the shelf', () => {
    turnController.recordError()
    resetTurnState()
    turnController.startMessage()

    turnController.recordToolStart('t1', 'Bash', 'seq 6', '{"command":"seq 6"}')
    turnController.recordToolComplete(
      't1',
      'Bash',
      undefined,
      undefined,
      0.2,
      undefined,
      '1\n2\n3\n… +3 lines (ctrl+o to expand)',
      '1\n2\n3\n4\n5\n6'
    )
    // a second tool with nothing to expand keeps the pairing aligned
    turnController.recordToolStart('t2', 'Read', 'a.ts')
    turnController.recordToolComplete('t2', 'Read', undefined, undefined, 0.1, undefined, 'Read 3 lines')

    const { finalMessages } = turnController.recordMessageComplete({ text: 'done' })
    const shelf = finalMessages.find(msg => msg.tools?.length)

    expect(shelf?.tools).toHaveLength(2)
    expect(shelf?.toolsVerbose).toHaveLength(2)
    expect(shelf?.toolsVerbose?.[0]).toContain('Result:\n1\n2\n3\n4\n5\n6')
    expect(shelf?.toolsVerbose?.[0]).toContain('Args:')
    expect(shelf?.toolsVerbose?.[1]).toBe('')

    turnController.recordError()
    resetTurnState()
  })

  it('keeps lockstep when a no-op structured diff falls back to the plain trail', () => {
    turnController.recordError()
    resetTurnState()
    turnController.startMessage()

    // A no-op Write (empty structuredPatch) can't render a diff → the tool
    // completion falls back onto pendingSegmentTools; the verbose sibling
    // must ride along or the NEXT tool's sibling misaligns.
    turnController.recordStructuredDiffToolComplete(
      { filePath: '/ws/x.ts', hunks: [], kind: 'update' } as never,
      'd1',
      'Write',
      undefined,
      0.1
    )
    turnController.recordToolStart('t2', 'Bash', 'seq 6', '{"command":"seq 6"}')
    turnController.recordToolComplete(
      't2',
      'Bash',
      undefined,
      undefined,
      0.2,
      undefined,
      '1\n2\n3\n… +3 lines (ctrl+o to expand)',
      '1\n2\n3\n4\n5\n6'
    )

    const { finalMessages } = turnController.recordMessageComplete({ text: 'done' })
    const shelf = finalMessages.find(msg => (msg.tools?.length ?? 0) >= 2)!

    // The Bash verbose sibling stays aligned to the Bash tool (index 1),
    // not shifted onto the Write row by the unpaired fallback push.
    const bashIdx = shelf.tools!.findIndex(line => line.startsWith('Bash'))
    expect(shelf.toolsVerbose?.[bashIdx]).toContain('Result:\n1\n2\n3\n4\n5\n6')

    turnController.recordError()
    resetTurnState()
  })
})

// ── shelf merge keeps lockstep ───────────────────────────────────────────────

describe('mergeToolShelfInto lockstep', () => {
  it('pads legacy messages so verbose siblings stay index-aligned', () => {
    const target: Msg = { kind: 'trail', role: 'system', text: '', tools: ['A(x) ✓'] } // legacy, no verbose

    const source: Msg = {
      kind: 'trail',
      role: 'system',
      text: '',
      tools: ['B(y) :: sum ✓'],
      toolsVerbose: ['B(y) :: Result:\nfull ✓']
    }

    const merged = mergeToolShelfInto(target, source)

    expect(merged.tools).toEqual(['A(x) ✓', 'B(y) :: sum ✓'])
    expect(merged.toolsVerbose).toEqual(['', 'B(y) :: Result:\nfull ✓'])
  })

  it('keeps the legacy shape when nothing has a verbose form', () => {
    const merged = mergeToolShelfInto(
      { kind: 'trail', role: 'system', text: '', tools: ['A ✓'] },
      { kind: 'trail', role: 'system', text: '', tools: ['B ✓'] }
    )

    expect(merged.toolsVerbose).toBeUndefined()
  })
})

// ── render + heights ─────────────────────────────────────────────────────────

describe('expanded rendering', () => {
  const trail = ['Bash(seq 6) :: 1\n2\n3\n… +3 lines (ctrl+o to expand) ✓']
  const verboseTrail = ['Bash(seq 6) (0.2s) :: Result:\n1\n2\n3\n4\n5\n6 ✓']

  it('collapsed shows the summary; expanded swaps in the full result', () => {
    const collapsed = stripAnsi(
      renderToString(
        React.createElement(ToolTrail, { detailsMode: 'collapsed', t: DEFAULT_THEME, trail, verboseTrail })
      )
    )

    expect(collapsed).toContain('+3 lines (ctrl+o to expand)')
    expect(collapsed).not.toContain('4') // verbose-only content stays hidden

    const expanded = stripAnsi(
      renderToString(
        React.createElement(ToolTrail, { detailsMode: 'expanded', t: DEFAULT_THEME, trail, verboseTrail })
      )
    )

    expect(expanded).toContain('Result:')
    expect(expanded).toContain('4')
    expect(expanded).not.toContain('ctrl+o to expand')
  })

  it('the per-message hash changes with the verbose sibling (cache invalidation)', async () => {
    const { messageHeightKey } = await import('../lib/virtualHeights.js')
    const compact: Msg = { kind: 'trail', role: 'system', text: '', tools: trail }
    const withVerbose: Msg = { ...compact, toolsVerbose: verboseTrail }

    expect(messageHeightKey(compact)).not.toBe(messageHeightKey(withVerbose))
  })

  it('height estimates count the variant that renders', () => {
    const msg: Msg = { kind: 'trail', role: 'system', text: '', tools: trail, toolsVerbose: verboseTrail }
    const opts = { compact: false, details: true, leadGap: false, withSeparator: false }

    const collapsed = estimatedMsgHeight(msg, 80, opts)
    const expanded = estimatedMsgHeight(msg, 80, { ...opts, toolsExpanded: true })

    expect(expanded).toBeGreaterThan(collapsed)
  })
})

import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Fake the agent-server child so tests can feed NDJSON protocol lines and
// observe the GatewayEvents (same harness as structuredDiff.test.ts).
const harness = vi.hoisted(() => ({ proc: null as null | EventEmitter, spawnCalls: [] as unknown[][] }))

vi.mock('node:child_process', () => ({
  spawn: (...args: unknown[]) => {
    harness.spawnCalls.push(args)

    return harness.proc
  }
}))

import { flattenToolResultContent, formatFileSize, formatToolResult, GatewayClient } from '../gatewayClient.js'

class FakeProc extends EventEmitter {
  kill = vi.fn()
  stderr = new PassThrough()
  stdin = new PassThrough()
  stdout = new PassThrough()

  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

// A truncated stand-in for the real payload. The bug was the transcript
// printing this envelope verbatim, wrapped over dozens of rows.
const IMAGE_CONTENT = [
  {
    source: { data: '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJ', media_type: 'image/jpeg', type: 'base64' },
    type: 'image'
  }
]

// ── formatFileSize: exact port of typescript/src/utils/format.ts ────────────

describe('formatFileSize', () => {
  it('matches the original ladder', () => {
    expect(formatFileSize(0)).toBe('0 bytes')
    expect(formatFileSize(512)).toBe('512 bytes')
    expect(formatFileSize(1024)).toBe('1KB')
    expect(formatFileSize(1536)).toBe('1.5KB')
    // Rolls over on the ROUNDED magnitude, so this is 1MB, not 1024KB.
    expect(formatFileSize(1048575)).toBe('1MB')
    expect(formatFileSize(1048576)).toBe('1MB')
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5MB')
    expect(formatFileSize(3 * 1024 * 1024 * 1024)).toBe('3GB')
  })
})

// ── flattenToolResultContent: the base64 leak itself ───────────────────────

describe('flattenToolResultContent', () => {
  it('never serializes an image block', () => {
    const out = flattenToolResultContent(IMAGE_CONTENT)
    expect(out).toBe('[image]')
    expect(out).not.toContain('base64')
    expect(out).not.toContain('/9j/4AAQ')
  })

  it('keeps text blocks and drops only the binary ones', () => {
    expect(
      flattenToolResultContent([{ text: 'before', type: 'text' }, ...IMAGE_CONTENT, { text: 'after', type: 'text' }])
    ).toBe('before\n[image]\nafter')
  })

  it('placeholders a document block', () => {
    expect(flattenToolResultContent([{ source: { data: 'JVBERi0x', type: 'base64' }, type: 'document' }])).toBe(
      '[document]'
    )
  })

  it('passes strings through untouched', () => {
    expect(flattenToolResultContent('     1\tconst a = 1')).toBe('     1\tconst a = 1')
  })

  it('still summarizes an unknown block shape rather than dropping it', () => {
    expect(flattenToolResultContent([{ type: 'mystery', value: 7 }])).toContain('mystery')
  })
})

// ── formatToolResult: the one-line render ──────────────────────────────────

describe('formatToolResult with an image envelope', () => {
  it('renders the original one-liner', () => {
    // FileReadTool/UI.tsx renderToolResultMessage case 'image'.
    expect(formatToolResult('Read', '[image]', false, undefined, { originalSize: 128_000 })).toBe(
      'Read image (125KB)'
    )
  })

  it('omits the size when the backend predates the envelope field', () => {
    expect(formatToolResult('Read', '[image]', false, undefined, {})).toBe('Read image')
  })

  it('omits the size rather than printing NaN when the number is unusable', () => {
    // This calls formatToolResult directly, so imageDisplay's wire-path guard is
    // NOT what saves it — the guard inside imageSummary is. Delete that one and
    // formatFileSize divides/toFixed()s its way to "Read image (NaNGB)".
    for (const bad of [Number.NaN, Infinity, -Infinity]) {
      expect(formatToolResult('Read', '[image]', false, undefined, { originalSize: bad })).toBe('Read image')
    }
  })

  it('does not hijack a normal text read', () => {
    expect(formatToolResult('Read', '     1\tconst a = 1\n     2\tconst b = 2')).toBe('Read 2 lines')
  })

  it('borrows the Read wording for any tool carrying an image envelope', () => {
    // Documents current behavior: `_display_tool_result` is shape-keyed and
    // global, so the label is not tool-scoped the way the reference's per-tool
    // renderToolResultMessage is. Read is the only producer today; if a second
    // one appears this expectation should change alongside a tool-aware label.
    expect(formatToolResult('SomeFutureTool', '[image]', false, undefined, { originalSize: 2048 })).toBe(
      'Read image (2KB)'
    )
  })

  it('lets a failed image read render as an error, not a summary', () => {
    expect(formatToolResult('Read', 'EISDIR: illegal operation on a directory', true, undefined, {
      originalSize: 128_000
    })).toContain('EISDIR')
  })
})

// ── End to end through the gateway ─────────────────────────────────────────

describe('GatewayClient image tool_result mapping', () => {
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

  it('renders "Read image (N)" and leaks no base64 anywhere in the payload', async () => {
    const payload = await completeTool(
      'i1',
      'Read',
      { file_path: '/tmp/blog-sources.png' },
      {
        message: { content: [{ content: IMAGE_CONTENT, tool_use_id: 'i1', type: 'tool_result' }] },
        tool_use_result: { originalSize: 128_000, type: 'image' },
        type: 'user'
      }
    )

    expect(payload.result_text).toBe('Read image (125KB)')
    // Read is deliberately non-expandable, so there is no raw copy either.
    expect(payload.result_raw).toBeUndefined()
    expect(JSON.stringify(payload)).not.toContain('/9j/4AAQ')
    expect(JSON.stringify(payload)).not.toContain('base64')
    expect(payload.error).toBeUndefined()
  })

  it('still degrades to a placeholder when the backend sends no envelope', async () => {
    const payload = await completeTool(
      'i2',
      'Read',
      { file_path: '/tmp/blog-sources.png' },
      { message: { content: [{ content: IMAGE_CONTENT, tool_use_id: 'i2', type: 'tool_result' }] }, type: 'user' }
    )

    expect(payload.result_text).toBe('[image]')
    expect(JSON.stringify(payload)).not.toContain('/9j/4AAQ')
  })

  it('does not pin one image envelope onto a later unrelated tool', async () => {
    await completeTool(
      'i3',
      'Read',
      { file_path: '/tmp/a.png' },
      {
        message: { content: [{ content: IMAGE_CONTENT, tool_use_id: 'i3', type: 'tool_result' }] },
        tool_use_result: { originalSize: 2048, type: 'image' },
        type: 'user'
      }
    )

    const second = await completeTool(
      'i4',
      'Bash',
      { command: 'echo hi' },
      { message: { content: [{ content: 'hi', tool_use_id: 'i4', type: 'tool_result' }] }, type: 'user' }
    )

    expect(second.result_text).toBe('hi')
  })
})

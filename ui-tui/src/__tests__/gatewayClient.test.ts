import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The clawcodex GatewayClient is an adapter that spawns `clawcodex
// agent-server --stdio` and maps its stdout NDJSON to hermes GatewayEvents.
// We fake the child process so the test can feed protocol lines on stdout and
// observe the emitted events. (The previous suite here tested an older
// WebSocket attach-mode client that the NDJSON rewrite in #572 removed.)
const harness = vi.hoisted(() => ({ proc: null as null | EventEmitter, spawnCalls: [] as unknown[][] }))

vi.mock('node:child_process', () => ({
  spawn: (...args: unknown[]) => {
    harness.spawnCalls.push(args)
    return harness.proc
  }
}))

import { GatewayClient } from '../gatewayClient.js'

class FakeProc extends EventEmitter {
  kill = vi.fn()
  stderr = new PassThrough()
  stdin = new PassThrough()
  stdout = new PassThrough()

  /** Feed one NDJSON protocol message to the client as a stdout line. */
  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

const toolUse = (id: string, name: string, input: unknown) => ({
  message: { content: [{ id, input, name, type: 'tool_use' }] },
  type: 'assistant'
})
const toolResult = (id: string, content: unknown, isError = false) => ({
  message: { content: [{ content, is_error: isError, tool_use_id: id, type: 'tool_result' }] },
  type: 'user'
})

const INIT = {
  cwd: '/ws',
  model: 'claude-test',
  protocol_version: '0.1.0',
  session_id: 's1',
  subtype: 'init',
  tools: [{ name: 'Read' }, { name: 'Bash' }],
  type: 'system'
}

describe('GatewayClient NDJSON adapter', () => {
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
    gw.drain() // subscribe so publish() emits live instead of buffering
  })

  afterEach(() => {
    gw.kill()
    if (prevWs === undefined) delete process.env.CLAWCODEX_WORKSPACE
    else process.env.CLAWCODEX_WORKSPACE = prevWs
  })

  const types = () => events.map(e => e.type)
  const last = (t: string) => [...events].reverse().find(e => e.type === t)
  // Emit a tool_use then await its tool.start (so toolInputs is populated),
  // then emit the matching tool_result and await its tool.complete.
  const runTool = async (id: string, name: string, input: unknown, result: unknown) => {
    proc.line(toolUse(id, name, input))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    proc.line(toolResult(id, result))
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())
    return last('tool.complete').payload
  }

  it('spawns the agent-server and emits gateway.ready + session.info on init', async () => {
    expect(harness.spawnCalls).toHaveLength(1)
    proc.line(INIT)
    await vi.waitFor(() => expect(types()).toContain('gateway.ready'))
    expect(types()).toContain('session.info')
    await expect(gw.request('session.create', {})).resolves.toMatchObject({ session_id: 's1' })
  })

  it('labels file tools with a workspace-relative path', async () => {
    proc.line(toolUse('t1', 'Read', { file_path: '/ws/src/foo.ts' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    expect(last('tool.start').payload).toMatchObject({ context: 'src/foo.ts', name: 'Read', tool_id: 't1' })
  })

  it('falls back to the basename for paths outside the workspace', async () => {
    proc.line(toolUse('t1', 'Read', { file_path: '/etc/hosts' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    expect(last('tool.start').payload.context).toBe('hosts')
  })

  it('labels Bash with its command (no path relativization)', async () => {
    proc.line(toolUse('t1', 'Bash', { command: 'ls -la' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    expect(last('tool.start').payload.context).toBe('ls -la')
  })

  it('labels search tools with the pattern, not the search directory', async () => {
    proc.line(toolUse('t1', 'Grep', { path: '/ws/src', pattern: 'TODO' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    expect(last('tool.start').payload.context).toBe('TODO')
  })

  // Read's numbered output is `f"{i}\t{line}"` joined by "\n" — no leading pad,
  // no trailing newline (src/tool_system/tools/read.py).
  it('collapses a Read result to a line count', async () => {
    const p = await runTool('t1', 'Read', { file_path: '/ws/a.ts' }, '1\tconst a = 1\n2\tconst b = 2\n3\tconst c = 3')
    expect(p.result_text).toBe('Read 3 lines')
  })

  it('uses the singular for a one-line Read result', async () => {
    const p = await runTool('t1', 'Read', { file_path: '/ws/a.ts' }, '1\tonly line')
    expect(p.result_text).toBe('Read 1 line')
  })

  // Read's non-text acks aren't `N\t…` numbered output, so they must NOT be
  // collapsed (the empty-file case would otherwise become a false "Read 1 line"
  // and bury the warning).
  it('does not collapse the empty-file warning', async () => {
    const warning = '<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>'
    const p = await runTool('t1', 'Read', { file_path: '/ws/empty.ts' }, warning)
    expect(p.result_text).toBe(warning)
  })

  it('does not collapse the file_unchanged dedup stub', async () => {
    const stub = 'File unchanged since last read. The content from the earlier Read tool_result in this conversation is still current — refer to that instead of re-reading.'
    const p = await runTool('t1', 'Read', { file_path: '/ws/a.ts' }, stub)
    expect(p.result_text).toBe(stub)
  })

  it('passes non-Read results through unchanged', async () => {
    const p = await runTool('t1', 'Bash', { command: 'echo hi' }, 'hi\n')
    expect(p.result_text).toBe('hi\n')
  })

  it('keeps a failed Read result visible instead of collapsing it to a line count', async () => {
    proc.line(toolUse('t1', 'Read', { file_path: '/ws/missing.ts' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    proc.line(toolResult('t1', 'File does not exist.', true))
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())
    expect(last('tool.complete').payload.result_text).toBe('File does not exist.')
  })

  it('attaches an inline diff for Edit results', async () => {
    const p = await runTool('t1', 'Edit', { file_path: '/ws/a.ts', new_string: 'b', old_string: 'a' }, 'ok')
    expect(p.name).toBe('Edit')
    expect(p.inline_diff).toContain('-a')
    expect(p.inline_diff).toContain('+b')
  })
})

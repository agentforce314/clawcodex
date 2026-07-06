import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// /advisor adapter coverage: dispatchSlash('advisor') ↔ the backend `advisor`
// control subtype (exec contract — the command's text is always printed as a
// system line), plus its presence in the slash catalog/menu.
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

  sent: any[] = []

  constructor() {
    super()
    this.stdin.on('data', chunk => {
      for (const line of String(chunk).split('\n')) {
        if (line.trim()) {
          this.sent.push(JSON.parse(line))
        }
      }
    })
  }

  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

const flush = () => new Promise(resolve => setTimeout(resolve, 0))

describe('/advisor gateway adapter', () => {
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

    if (prevWs === undefined) {delete process.env.CLAWCODEX_WORKSPACE}
    else {process.env.CLAWCODEX_WORKSPACE = prevWs}
  })

  const replyToLastControl = async (response: unknown) => {
    await flush()
    const req = proc.sent.at(-1)

    expect(req?.type).toBe('control_request')
    proc.line({
      response: { request_id: req.request_id, response, subtype: 'success' },
      type: 'control_response'
    })
    await flush()
  }

  it('SET maps to the advisor control and prints the reply text', async () => {
    const p = gw.request('command.dispatch', { arg: 'zai:glm-5.2', name: 'advisor' })
    await replyToLastControl({
      ok: true,
      text: 'Advisor set to zai:glm-5.2. Will run client-side (separate API call).'
    })

    const d: any = await p

    expect(proc.sent.at(-1).request).toEqual({ arg: 'zai:glm-5.2', subtype: 'advisor' })
    expect(d.type).toBe('exec')
    expect(d.output).toContain('Advisor set to zai:glm-5.2')
    expect(d.output).toContain('client-side')
  })

  it('bare /advisor is a status query with an empty arg', async () => {
    const p = gw.request('command.dispatch', { name: 'advisor' })
    await replyToLastControl({ ok: true, text: 'Advisor: not set' })

    const d: any = await p

    expect(proc.sent.at(-1).request).toEqual({ arg: '', subtype: 'advisor' })
    expect(d.type).toBe('exec')
    expect(d.output).toContain('Advisor: not set')
  })

  it('slash.exec form routes /advisor the same way', async () => {
    const p = gw.request('slash.exec', { command: 'advisor off', session_id: 's1' })
    await replyToLastControl({ ok: true, text: 'Advisor disabled (was zai:glm-5.2).' })

    const d: any = await p

    expect(proc.sent.at(-1).request).toEqual({ arg: 'off', subtype: 'advisor' })
    expect(d.type).toBe('exec')
    expect(d.output).toContain('Advisor disabled')
  })

  it('backend error replies surface their error text', async () => {
    const p = gw.request('command.dispatch', { arg: '', name: 'advisor' })
    await replyToLastControl({ error: 'boom', ok: false })

    const d: any = await p

    expect(d.type).toBe('exec')
    expect(d.output).toContain('boom')
  })

  it('/advisor appears in the slash completion menu with its hint', async () => {
    const p = gw.request('complete.slash', { text: '/adv' })
    // complete.slash first fetches the backend's workflow commands.
    await replyToLastControl({ commands: [] })

    const r: any = await p
    const item = r.items.find((i: any) => i.text === '/advisor')

    expect(item).toBeDefined()
    expect(item.hint).toContain('<provider>:<model>')
  })
})

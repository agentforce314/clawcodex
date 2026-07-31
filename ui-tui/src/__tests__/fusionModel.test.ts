import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Same fake-child harness as gatewayClient.test.ts: the adapter spawns
// `clawcodex agent-server --stdio`, so the test feeds protocol lines on the
// fake stdout and answers the control_requests it sees on stdin.
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

  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

const INIT_FUSED = {
  cwd: '/ws',
  // A fused session reports the BASE model here…
  fusion: 'deepseek-v4-pro-V',   // …and the fusion model's name separately.
  model: 'deepseek-v4-pro',
  protocol_version: '0.1.0',
  session_id: 's1',
  subtype: 'init',
  tools: [{ name: 'Read' }],
  type: 'system'
}

describe('fusion models', () => {
  const prevWs = process.env.CLAWCODEX_WORKSPACE
  let events: any[]
  let gw: GatewayClient
  let proc: FakeProc
  let seen: any[]

  beforeEach(() => {
    process.env.CLAWCODEX_WORKSPACE = '/ws'
    proc = new FakeProc()
    harness.proc = proc
    harness.spawnCalls = []
    events = []
    seen = []
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

  const last = (t: string) => [...events].reverse().find(e => e.type === t)

  const stdinFrames = (): any[] => {
    const raw = (proc.stdin as any).read()?.toString() ?? ''

    return raw.split('\n').filter(Boolean).map((l: string) => JSON.parse(l))
  }

  const replyToControl = async (subtype: string, response: unknown) => {
    let req: any

    await vi.waitFor(() => {
      seen.push(...stdinFrames())
      req = seen.find(f => f.type === 'control_request' && f.request?.subtype === subtype)
      expect(req).toBeTruthy()
    })
    proc.line({ response: { request_id: req.request_id, response }, type: 'control_response' })
  }

  // ── the model line ────────────────────────────────────────────────────────

  it('shows the fusion name as the session model, not the base id', async () => {
    proc.line(INIT_FUSED)
    // The fusion name is what the user selected and the only signal that
    // images are being routed through a second model.
    await vi.waitFor(() => expect(last('session.info')?.payload?.model).toBe('deepseek-v4-pro-V'))
  })

  it('falls back to the plain model when not fused', async () => {
    proc.line({ ...INIT_FUSED, fusion: '' })
    await vi.waitFor(() => expect(last('session.info')?.payload?.model).toBe('deepseek-v4-pro'))
  })

  it('tolerates a backend that omits the fusion field', async () => {
    const { fusion: _fusion, ...noFusion } = INIT_FUSED

    proc.line(noFusion)
    await vi.waitFor(() => expect(last('session.info')?.payload?.model).toBe('deepseek-v4-pro'))
  })

  // ── /fusion relay ─────────────────────────────────────────────────────────

  it('relays /fusion to the backend control and prints its text', async () => {
    const p = gw.request('slash.exec', { command: 'fusion list' })

    await replyToControl('fusion', { ok: true, text: 'Fusion models (1):\n  dsv = a:b + vision:c:d' })
    await expect(p).resolves.toEqual({
      output: 'Fusion models (1):\n  dsv = a:b + vision:c:d',
      type: 'exec'
    })
  })

  it('surfaces a /fusion error when the backend refuses', async () => {
    const p = gw.request('slash.exec', { command: 'fusion list' })

    await replyToControl('fusion', { error: '/fusion is only available on single-session transports', ok: false })
    await expect(p).resolves.toMatchObject({ output: expect.stringContaining('single-session') })
  })

  it('lists /fusion in the slash-command menu', async () => {
    // SLASHES drives both recognition and the menu, so an entry missing here
    // means /fusion is untypeable in the TUI.
    const p = gw.request('complete.slash', { text: '/fus' })

    // The menu merges dynamic workflow commands in, so that control has to
    // be answered or the request never settles.
    await replyToControl('list_workflow_commands', { commands: [] })
    const r: any = await p

    expect(JSON.stringify(r)).toContain('/fusion')
    expect(JSON.stringify(r)).toContain('vision')   // the hint/description
  })

  // ── /model interaction ────────────────────────────────────────────────────

  // model.options now asks `list_model_providers` (the picker lists every
  // provider, not just the active one). The fusion contract is unchanged: the
  // reply carries the base id in `model` and the fusion name separately, and
  // the picker's "current" marker must follow the fusion name.
  const FUSED_CATALOG = {
    ok: true,
    provider: 'deepseek',
    providers: [
      {
        authenticated: true,
        is_current: true,
        models: ['dsv', 'deepseek-v4-pro'],
        name: 'DeepSeek',
        slug: 'deepseek',
        total_models: 2
      }
    ]
  }

  it('marks the fusion model as current in the /model picker', async () => {
    const p = gw.request('model.options', {})

    await replyToControl('list_model_providers', {
      ...FUSED_CATALOG,
      fusion: 'dsv',
      model: 'deepseek-v4-pro'
    })
    // Must point at the fusion entry the user selected, not the base row.
    await expect(p).resolves.toMatchObject({ model: 'dsv' })
  })

  it('leaves the picker on the plain model when not fused', async () => {
    const p = gw.request('model.options', {})

    await replyToControl('list_model_providers', {
      ...FUSED_CATALOG,
      fusion: '',
      model: 'deepseek-v4-pro'
    })
    await expect(p).resolves.toMatchObject({ model: 'deepseek-v4-pro' })
  })

  it('keeps the fusion marker on the get_settings fallback path', async () => {
    // An old backend that does not know list_model_providers answers null;
    // the synthesized single-provider row must still resolve the fusion name.
    vi.useFakeTimers()

    try {
      const p = gw.request('model.options', {})
      await vi.waitFor(() => {
        seen.push(...stdinFrames())
        expect(seen.find(f => f.request?.subtype === 'list_model_providers')).toBeTruthy()
      })
      await vi.advanceTimersByTimeAsync(5_100)
      await vi.waitFor(() => {
        seen.push(...stdinFrames())
        expect(seen.find(f => f.request?.subtype === 'get_settings')).toBeTruthy()
      })
      const req = seen.find(f => f.request?.subtype === 'get_settings')!
      proc.line({
        response: {
          request_id: req.request_id,
          response: {
            available_models: ['dsv', 'deepseek-v4-pro'],
            fusion: 'dsv',
            model: 'deepseek-v4-pro',
            provider: 'deepseek'
          }
        },
        type: 'control_response'
      })

      await expect(p).resolves.toMatchObject({ model: 'dsv' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('reports a refused /model switch instead of claiming success', async () => {
    // set_model declines for several actionable reasons (a disabled fusion
    // model, missing credentials for a half, an active turn). Printing
    // "Model set to X" regardless told the user the opposite of the truth.
    const p = gw.request('slash.exec', { command: 'model dsv' })

    await replyToControl('set_model', { error: "fusion model 'dsv' is disabled — run `/fusion enable dsv` first", ok: false })
    const r: any = await p

    expect(r.output).toContain('is disabled')
    expect(r.output).not.toContain('Model set to')
  })

  it('echoes the model the backend actually applied', async () => {
    const p = gw.request('slash.exec', { command: 'model dsv' })

    await replyToControl('set_model', { fusion: 'dsv', model: 'dsv', ok: true })
    await expect(p).resolves.toMatchObject({ output: 'Model set to dsv.' })
  })

  it('appends a set_model warning when the backend sends one', async () => {
    const p = gw.request('slash.exec', { command: 'model odd-id' })

    await replyToControl('set_model', { model: 'odd-id', ok: true, warning: 'not in the model list' })
    await expect(p).resolves.toMatchObject({
      output: expect.stringContaining('not in the model list')
    })
  })
})

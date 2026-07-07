import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// /goal adapter coverage: dispatchSlash('goal'|'subgoal') ↔ the backend
// `goal`/`subgoal` control subtypes (hermes send/exec contract), and the
// system/goal_status → status.update kind:'goal' event mapping the ported
// createGatewayEventHandler renders.
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

describe('/goal gateway adapter', () => {
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

  it('SET maps a kickoff reply to the hermes send contract', async () => {
    const p = gw.request('command.dispatch', { arg: 'ship the feature', name: 'goal' })
    await replyToLastControl({
      active: true,
      kickoff: 'ship the feature',
      notice: '◎ Goal set (20-turn budget): ship the feature',
      ok: true,
      text: '◎ Goal set (20-turn budget): ship the feature'
    })

    const d: any = await p

    expect(proc.sent.at(-1).request).toEqual({ arg: 'ship the feature', subtype: 'goal' })
    expect(d.type).toBe('send')
    expect(d.message).toBe('ship the feature')
    expect(d.notice).toContain('Goal set')
  })

  it('status/clear replies map to exec output', async () => {
    const p = gw.request('command.dispatch', { arg: 'status', name: 'goal' })
    await replyToLastControl({ active: false, ok: true, text: 'No active goal. Set one with /goal <condition>.' })

    const d: any = await p

    expect(d.type).toBe('exec')
    expect(d.output).toContain('No active goal')
  })

  it('gate refusals surface the reason as exec output', async () => {
    const p = gw.request('command.dispatch', { arg: 'do things', name: 'goal' })
    await replyToLastControl({ active: false, error: '/goal requires a trusted workspace.', ok: false, text: '/goal requires a trusted workspace.' })

    const d: any = await p

    expect(d.type).toBe('exec')
    expect(d.output).toContain('trusted workspace')
  })

  it('slash.exec form routes /goal the same way', async () => {
    const p = gw.request('slash.exec', { command: 'goal pause', session_id: 's1' })
    await replyToLastControl({ active: false, ok: true, text: '⏸ Goal paused: x' })

    const d: any = await p

    expect(proc.sent.at(-1).request).toEqual({ arg: 'pause', subtype: 'goal' })
    expect(d.type).toBe('exec')
    expect(d.output).toContain('paused')
  })

  it('subgoal maps to the subgoal control', async () => {
    const p = gw.request('command.dispatch', { arg: 'also update docs', name: 'subgoal' })
    await replyToLastControl({ active: true, ok: true, text: '✓ Added subgoal 1: also update docs' })

    const d: any = await p

    expect(proc.sent.at(-1).request).toEqual({ arg: 'also update docs', subtype: 'subgoal' })
    expect(d.type).toBe('exec')
    expect(d.output).toContain('Added subgoal 1')
  })

  it('system/goal_status maps to status.update kind goal', async () => {
    proc.line({
      goal_active: true,
      message: '↻ Continuing toward goal (1/20): tests still red',
      session_id: 's1',
      subtype: 'goal_status',
      type: 'system'
    })
    await flush()

    const ev = events.find(e => e.type === 'status.update')

    expect(ev).toBeTruthy()
    expect(ev.payload.kind).toBe('goal')
    expect(ev.payload.text).toContain('Continuing toward goal')
  })

  it('SET reply snapshot publishes a goal.state event for the indicator', async () => {
    const p = gw.request('command.dispatch', { arg: 'ship the feature', name: 'goal' })
    await replyToLastControl({
      active: true,
      goal: { created_at: 1_751_800_000, goal: 'ship the feature', max_turns: 20, status: 'active', turns_used: 0 },
      goal_rev: 3,
      kickoff: 'ship the feature',
      notice: '◎ Goal set (20-turn budget): ship the feature',
      ok: true,
      text: '◎ Goal set (20-turn budget): ship the feature'
    })
    await p

    const ev = events.find(e => e.type === 'goal.state')

    expect(ev).toBeTruthy()
    expect(ev.payload.goal).toMatchObject({ created_at: 1_751_800_000, status: 'active' })
    expect(ev.payload.rev).toBe(3)
  })

  it('clear reply with a null snapshot publishes goal.state null', async () => {
    const p = gw.request('command.dispatch', { arg: 'clear', name: 'goal' })
    await replyToLastControl({ active: false, goal: null, ok: true, text: '✓ Goal cleared.' })
    await p

    const ev = events.find(e => e.type === 'goal.state')

    expect(ev).toBeTruthy()
    expect(ev.payload.goal).toBeNull()
  })

  it('a reply without the snapshot field (older backend) publishes nothing', async () => {
    const p = gw.request('command.dispatch', { arg: 'status', name: 'goal' })
    await replyToLastControl({ active: true, ok: true, text: '◎ Goal (active): x' })
    await p

    expect(events.find(e => e.type === 'goal.state')).toBeUndefined()
  })

  it('goal_status events refresh the indicator from their snapshot', async () => {
    proc.line({
      goal: { created_at: 1_751_800_000, goal: 'x', max_turns: 20, status: 'paused', turns_used: 3 },
      goal_active: false,
      message: '⏸ Goal paused — turn interrupted.',
      session_id: 's1',
      subtype: 'goal_status',
      type: 'system'
    })
    await flush()

    const ev = events.find(e => e.type === 'goal.state')

    expect(ev).toBeTruthy()
    expect(ev.payload.goal).toMatchObject({ status: 'paused', turns_used: 3 })
  })

  it('legacy goal_status without a snapshot clears only on goal_active=false', async () => {
    proc.line({
      goal_active: true,
      message: '↻ Continuing toward goal (1/20): wip',
      session_id: 's1',
      subtype: 'goal_status',
      type: 'system'
    })
    await flush()
    expect(events.find(e => e.type === 'goal.state')).toBeUndefined()

    proc.line({
      goal_active: false,
      message: '✓ Goal achieved: all pass',
      session_id: 's1',
      subtype: 'goal_status',
      type: 'system'
    })
    await flush()

    const ev = events.find(e => e.type === 'goal.state')

    expect(ev).toBeTruthy()
    expect(ev.payload.goal).toBeNull()
  })

  it('/clear drops the indicator (legacy success reply without the rider)', async () => {
    const p = gw.request('command.dispatch', { arg: '', name: 'clear' })
    await replyToLastControl({ ok: true })
    const d: any = await p

    const ev = events.find(e => e.type === 'goal.state')

    expect(ev).toBeTruthy()
    expect(ev.payload.goal).toBeNull()
    expect(d.output).toContain('cleared')
  })

  it('/clear success routes the new goal rider (with rev) through', async () => {
    const p = gw.request('command.dispatch', { arg: '', name: 'clear' })
    await replyToLastControl({ cost: {}, goal: null, goal_rev: 9, ok: true, session_turns: 0 })
    await p

    const ev = events.find(e => e.type === 'goal.state')

    expect(ev).toBeTruthy()
    expect(ev.payload.goal).toBeNull()
    expect(ev.payload.rev).toBe(9)
  })

  it('a REJECTED /clear (active turn) leaves the indicator alone', async () => {
    const p = gw.request('command.dispatch', { arg: '', name: 'clear' })
    await replyToLastControl({ error: 'cannot clear during an active turn', ok: false })
    const d: any = await p

    expect(events.find(e => e.type === 'goal.state')).toBeUndefined()
    expect(d.output).toContain('cannot clear during an active turn')
  })

  it('/goal and /subgoal are in the slash catalog', async () => {
    proc.line({
      cwd: '/ws', model: 'm', protocol_version: '0.1.0', session_id: 's1',
      subtype: 'init', tools: [], type: 'system'
    })
    await flush()
    // Workflow-command fetch rides the catalog request; answer it empty.
    const catalogP = gw.request<any>('commands.catalog', {})
    await replyToLastControl({ commands: [] })

    const catalog = await catalogP

    expect(catalog.canon['/goal']).toBe('/goal')
    expect(catalog.canon['/subgoal']).toBe('/subgoal')
    expect(catalog.hints['/goal']).toContain('condition')
  })
})

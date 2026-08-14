/**
 * The wiring, end to end against a fake socket: connect → create a session →
 * fold pushes into the transcript → queue and drain.
 *
 * This is the only test that exercises the real `GatewayClient` and the real
 * actions together, so it is where routing bugs show up — an event applied to
 * the wrong session, a queued prompt that never drains, a turn that stays
 * locked after a failed submit.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { GatewayClient } from '../gateway/client.ts'
import {
  createSession,
  dequeue,
  setGatewayClient,
  start,
  submitPrompt,
} from './actions.ts'
import { $queue, $sessionId, $transcript } from './store.ts'
import { emptyTranscript, type AssistantNode } from './transcript.ts'

/** Answers every RPC from a canned table and lets tests push events. */
class FakeGateway {
  static current: FakeGateway | null = null

  sent: { id: string; method: string; params: Record<string, unknown> }[] = []
  results: Record<string, unknown> = {}
  failing = new Set<string>()

  private readonly listeners = new Map<string, Set<(event: unknown) => void>>()

  readyState = 1

  constructor() {
    FakeGateway.current = this
  }

  addEventListener(type: string, handler: (event: unknown) => void): void {
    const set = this.listeners.get(type) ?? new Set()
    set.add(handler)
    this.listeners.set(type, set)

    if (type === 'open') queueMicrotask(() => handler({}))
  }

  removeEventListener(type: string, handler: (event: unknown) => void): void {
    this.listeners.get(type)?.delete(handler)
  }

  close(): void {
    this.readyState = 3
  }

  send(raw: string): void {
    const frame = JSON.parse(raw) as {
      id: string
      method: string
      params: Record<string, unknown>
    }
    this.sent.push(frame)

    const reply = this.failing.has(frame.method)
      ? { error: { message: `${frame.method} refused` }, id: frame.id }
      : { id: frame.id, result: this.results[frame.method] ?? {} }

    queueMicrotask(() => {
      this.deliver(reply)
    })
  }

  /** Push a server event, as the gateway would. */
  emit(type: string, payload: unknown, sessionId?: string): void {
    this.deliver({
      method: 'event',
      params: { payload, session_id: sessionId, type },
    })
  }

  methods(): string[] {
    return this.sent.map(frame => frame.method)
  }

  private deliver(frame: unknown): void {
    for (const handler of [...(this.listeners.get('message') ?? [])]) {
      handler({ data: JSON.stringify(frame) })
    }
  }
}

/** Drain the microtask queue so canned replies land. */
const settle = async (): Promise<void> => {
  for (let i = 0; i < 8; i += 1) await Promise.resolve()
}

async function connect(results: Record<string, unknown> = {}): Promise<FakeGateway> {
  const client = new GatewayClient({
    connectTimeoutMs: 1000,
    requestTimeoutMs: 1000,
    socketFactory: () => new FakeGateway() as unknown as WebSocket,
  })
  setGatewayClient(client)

  const startup = start()
  await settle()

  const gateway = FakeGateway.current

  if (gateway === null) throw new Error('no socket was opened')

  gateway.results = {
    'commands.catalog': { pairs: [['/clear', 'Clear the conversation']] },
    'model.options': { model: 'deepseek-v4-pro', provider: 'deepseek' },
    'projects.tree': { projects: [] },
    'session.create': { info: { model: 'deepseek-v4-pro' }, session_id: 'S1' },
    'session.usage': {},
    ...results,
  }

  await startup
  await settle()

  return gateway
}

beforeEach(() => {
  window.localStorage.clear()
  window.__CLAWCODEX_SESSION_TOKEN__ = 'test-token'
  $transcript.set(emptyTranscript())
  $sessionId.set(null)
  $queue.set([])
})

afterEach(() => {
  setGatewayClient(null)
  FakeGateway.current = null
})

describe('start', () => {
  it('connects and pulls the catalogs the chrome needs', async () => {
    const gateway = await connect()

    expect(gateway.methods()).toEqual(
      expect.arrayContaining(['projects.tree', 'model.options', 'commands.catalog']),
    )
  })
})

describe('submitPrompt', () => {
  it('creates a session on the first prompt and sends it', async () => {
    const gateway = await connect()

    await submitPrompt('hello there')
    await settle()

    expect($sessionId.get()).toBe('S1')

    const submit = gateway.sent.find(frame => frame.method === 'prompt.submit')
    expect(submit?.params).toEqual({ session_id: 'S1', text: 'hello there' })

    // The user bubble appears immediately and the turn locks — before any
    // server event confirms it.
    expect($transcript.get().nodes).toHaveLength(1)
    expect($transcript.get().running).toBe(true)
  })

  it('folds the turn into the transcript', async () => {
    const gateway = await connect()

    await submitPrompt('hi')
    await settle()

    gateway.emit('message.delta', { text: 'Hel' }, 'S1')
    gateway.emit('message.delta', { text: 'lo' }, 'S1')
    gateway.emit('message.complete', { status: 'ok', text: 'Hello' }, 'S1')
    await settle()

    const nodes = $transcript.get().nodes
    expect(nodes.map(node => node.kind)).toEqual(['user', 'assistant'])
    expect((nodes[1] as AssistantNode).text).toBe('Hello')
    expect($transcript.get().running).toBe(false)
  })

  it('ignores events from another session', async () => {
    const gateway = await connect()

    await submitPrompt('hi')
    await settle()

    gateway.emit('message.delta', { text: 'not mine' }, 'OTHER')
    await settle()

    expect($transcript.get().nodes.map(node => node.kind)).toEqual(['user'])
  })

  it('unlocks the composer when the submit is refused', async () => {
    const gateway = await connect()
    gateway.failing.add('prompt.submit')

    await submitPrompt('hi')
    await settle()

    expect($transcript.get().running).toBe(false)
  })

  it('ignores an empty draft', async () => {
    const gateway = await connect()

    await submitPrompt('   ')
    await settle()

    expect(gateway.methods()).not.toContain('session.create')
  })
})

describe('queue', () => {
  it('holds a prompt typed mid-turn and sends it when the turn ends', async () => {
    const gateway = await connect()

    await submitPrompt('first')
    await settle()
    await submitPrompt('second')
    await settle()

    expect($queue.get()).toEqual(['second'])
    expect(gateway.methods().filter(method => method === 'prompt.submit')).toHaveLength(1)

    gateway.emit('message.complete', { status: 'ok', text: 'done' }, 'S1')
    await settle()

    expect($queue.get()).toEqual([])
    const submits = gateway.sent.filter(frame => frame.method === 'prompt.submit')
    expect(submits.map(frame => frame.params.text)).toEqual(['first', 'second'])
  })

  it('drops a queued prompt on request', async () => {
    await connect()

    await submitPrompt('first')
    await settle()
    await submitPrompt('second')
    await submitPrompt('third')
    await settle()

    dequeue(0)
    expect($queue.get()).toEqual(['third'])
  })
})

describe('createSession', () => {
  it('clears the previous conversation before adopting the new session', async () => {
    const gateway = await connect()

    await submitPrompt('hi')
    await settle()
    gateway.emit('message.complete', { status: 'ok', text: 'reply' }, 'S1')
    await settle()
    expect($transcript.get().nodes.length).toBeGreaterThan(0)

    gateway.results['session.create'] = { session_id: 'S2' }
    await createSession()
    await settle()

    expect($sessionId.get()).toBe('S2')
    expect($transcript.get().nodes).toEqual([])
  })
})

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
  setDefaultProvider,
  setModel,
  dequeue,
  setGatewayClient,
  start,
  submitPrompt,
} from './actions.ts'
import { $notice, $providers, $queue, $sessionId, $sessionLoading, $transcript } from './store.ts'
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
  $providers.set({})
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

  it('spawns on the model picked before there was a session', async () => {
    // The bug this covers: `setModel` with no session stores the choice and
    // says it "rides the next session.create", but nothing carried it — so
    // picking a deepseek model spawned the session on the config default
    // provider (anthropic), which then 400s on the first turn.
    const gateway = await connect()

    await setModel('deepseek-v4-flash', 'deepseek')
    await createSession({ cwd: '/repo' })
    await settle()

    const create = gateway.sent.find(frame => frame.method === 'session.create')

    expect(create?.params).toMatchObject({
      cwd: '/repo',
      model: 'deepseek-v4-flash',
      provider: 'deepseek',
    })
  })

  it('lets an explicit option win over the picked model', async () => {
    // A caller naming a model means it.
    const gateway = await connect()

    await setModel('deepseek-v4-flash', 'deepseek')
    await createSession({ model: 'gpt-5.6-luna', provider: 'openai' })
    await settle()

    const create = gateway.sent.find(frame => frame.method === 'session.create')

    expect(create?.params).toMatchObject({ model: 'gpt-5.6-luna', provider: 'openai' })
  })

  it('clears the loading flag when the create fails', async () => {
    // Otherwise a failed create leaves "Loading session…" over an empty
    // transcript with no way back to the composer.
    const gateway = await connect()

    $sessionLoading.set(true)
    gateway.failing.add('session.create')
    await createSession()
    await settle()

    expect($sessionLoading.get()).toBe(false)
  })
})

describe('setDefaultProvider', () => {
  const REPLIES = {
    'provider.set_default': { default: 'deepseek', model: 'deepseek-v4-pro', ok: true },
    'provider.list': { default: 'deepseek', providers: [] },
    'config.set': { ok: true, value: 'deepseek-v4-pro' },
  }

  /** A live session that has not been used, running the outgoing default. */
  function seatUntouchedSession(provider = 'openai'): void {
    $sessionId.set('S1')
    $transcript.set({
      ...emptyTranscript(),
      info: { model: 'gpt-5.6-luna', provider },
    })
    $providers.set({ default: 'openai', providers: [] })
  }

  const switchFrame = (gateway: FakeGateway) =>
    gateway.sent.find(frame => frame.method === 'config.set' && frame.params.key === 'model')

  it('moves an unused session onto the new default', async () => {
    // "New session" creates its session eagerly, so the welcome screen has
    // one — and its own model outranks the catalog on the chip. Without the
    // switch the composer keeps naming the provider just replaced.
    const gateway = await connect(REPLIES)
    seatUntouchedSession()

    await setDefaultProvider('deepseek')
    await settle()

    expect(switchFrame(gateway)?.params.value).toBe('deepseek-v4-pro --provider deepseek')
  })

  it('leaves a session that has been used alone', async () => {
    const gateway = await connect(REPLIES)
    seatUntouchedSession()
    $transcript.set({
      ...$transcript.get(),
      nodes: [{ id: 'n1', kind: 'user', text: 'hello' }] as never,
    })

    await setDefaultProvider('deepseek')
    await settle()

    expect(switchFrame(gateway)).toBeUndefined()
  })

  it('switches even when the client cannot tell what the session inherited', async () => {
    // The state that shipped broken: an earlier version required
    // `info.provider` to equal the outgoing default, and those two facts
    // arrive on different round-trips — in the field the match silently
    // failed and the chip kept naming the replaced provider. An unused
    // session has nothing to preserve either way.
    const gateway = await connect(REPLIES)
    $sessionId.set('S1')
    $transcript.set(emptyTranscript())
    $providers.set({})

    await setDefaultProvider('deepseek')
    await settle()

    expect(switchFrame(gateway)?.params.value).toBe('deepseek-v4-pro --provider deepseek')
  })

  it('does not claim the new default when the switch was refused', async () => {
    // The refusal has to stay on screen: this session is still on the old
    // provider, and announcing the new one over it would hide that.
    const gateway = await connect({
      ...REPLIES,
      'config.set': { error: 'deepseek is not configured', ok: false },
    })
    seatUntouchedSession()

    await setDefaultProvider('deepseek')
    await settle()

    expect(switchFrame(gateway)).toBeDefined()
    expect($notice.get()).toMatchObject({ text: 'deepseek is not configured', tone: 'error' })
  })

  it('says which provider new sessions start on, after any switch', async () => {
    // The switch reports its own model change; the durable fact has to win
    // the line, or the confirmation the user asked for is overwritten.
    await connect(REPLIES)
    seatUntouchedSession()

    await setDefaultProvider('deepseek')
    await settle()

    expect($notice.get()).toMatchObject({ text: 'New sessions start on deepseek.' })
  })
})

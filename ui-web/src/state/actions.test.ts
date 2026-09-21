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
  attachImage,
  clearSession,
  createSession,
  dequeue,
  resumeSession,
  setDefaultProvider,
  setEffort,
  setGatewayClient,
  setModel,
  start,
  submitPrompt,
} from './actions.ts'
import {
  $models,
  $notice,
  $pendingModel,
  $providers,
  $queue,
  $sessionAttaching,
  $sessionId,
  $sessionLoading,
  $sessionTitle,
  $storedSessionId,
  $transcript,
  $workspace,
} from './store.ts'
import { emptyTranscript, type AssistantNode } from './transcript.ts'

/** Answers every RPC from a canned table and lets tests push events. */
class FakeGateway {
  static current: FakeGateway | null = null

  sent: { id: string; method: string; params: Record<string, unknown> }[] = []
  results: Record<string, unknown> = {}
  /** Per-call answers, consumed in order before `results` is consulted. */
  sequences: Record<string, unknown[]> = {}
  failing = new Set<string>()

  private readonly heldReplies = new Map<string, unknown[]>()

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

    const queued = this.sequences[frame.method]
    const reply = this.failing.has(frame.method)
      ? { error: { message: `${frame.method} refused` }, id: frame.id }
      : {
          id: frame.id,
          result: queued !== undefined && queued.length > 0 ? queued.shift() : (this.results[frame.method] ?? {}),
        }

    const held = this.heldReplies.get(frame.method)

    if (held !== undefined) held.push(reply)
    else {
      queueMicrotask(() => {
        this.deliver(reply)
      })
    }
  }

  hold(method: string): void {
    this.heldReplies.set(method, [])
  }

  release(method: string): void {
    const replies = this.heldReplies.get(method) ?? []
    this.heldReplies.delete(method)

    for (const reply of replies) this.deliver(reply)
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

const DEFAULT_RESULTS: Record<string, unknown> = {
  'commands.catalog': { pairs: [['/clear', 'Clear the conversation']] },
  'model.options': { model: 'deepseek-v4-pro', provider: 'deepseek' },
  'projects.tree': { projects: [] },
  'session.create': { info: { model: 'deepseek-v4-pro' }, session_id: 'S1' },
  'session.usage': {},
}

async function connect(results: Record<string, unknown> = {}): Promise<FakeGateway> {
  const client = new GatewayClient({
    connectTimeoutMs: 1000,
    requestTimeoutMs: 1000,
    // The table is in place before the first request goes out: boot itself
    // asks (the catalogs, and a remembered session), not only the tests.
    socketFactory: () =>
      Object.assign(new FakeGateway(), {
        results: { ...DEFAULT_RESULTS, ...results },
      }) as unknown as WebSocket,
  })
  setGatewayClient(client)

  const startup = start()
  await settle()

  const gateway = FakeGateway.current

  if (gateway === null) throw new Error('no socket was opened')

  await startup
  await settle()

  return gateway
}

beforeEach(() => {
  window.localStorage.clear()
  window.__CLAWCODEX_SESSION_TOKEN__ = 'test-token'
  $transcript.set(emptyTranscript())
  $sessionId.set(null)
  $storedSessionId.set(null)
  $providers.set({})
  $pendingModel.set(null)
  $models.set({})
  $notice.set({ text: '', tone: 'info' })
  $queue.set([])
  $sessionAttaching.set(false)
  $sessionTitle.set('')
  $workspace.set('')
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

describe('sent image previews', () => {
  it('carries accepted image bytes into the user row while keeping the wire prompt intact', async () => {
    const gateway = await connect({ 'image.attach': { attached: true, id: 2 } })
    await createSession()
    expect(await attachImage(new Blob(['image'], { type: 'image/png' }), 'shot.png')).toBe(2)

    await submitPrompt('[Image #2] describe this')

    expect($transcript.get().nodes[0]).toMatchObject({
      text: '[Image #2] describe this',
      images: [{ name: 'shot.png', placeholder: '[Image #2]', url: 'data:image/png;base64,aW1hZ2U=' }],
    })
    expect(gateway.sent.find(frame => frame.method === 'prompt.submit')?.params.text)
      .toBe('[Image #2] describe this')
  })

  it('does not render an image after its chip was removed, including in later turns', async () => {
    const gateway = await connect({ 'image.attach': { attached: true, id: 2 } })
    await createSession()
    await attachImage(new Blob(['image'], { type: 'image/png' }), 'shot.png')
    await submitPrompt('no attachment')
    expect($transcript.get().nodes[0]).not.toHaveProperty('images')

    gateway.emit('message.complete', { status: 'ok' }, 'S1')
    await settle()
    await submitPrompt('[Image #2] just a text reference')
    expect($transcript.get().nodes.at(-1)).not.toHaveProperty('images')
  })

  it('keeps preview bytes while an attached prompt waits for the running turn', async () => {
    const gateway = await connect({ 'image.attach': { attached: true, id: 3 } })
    await submitPrompt('first question')
    await attachImage(new Blob(['image'], { type: 'image/png' }), 'queued.png')
    await submitPrompt('[Image #3] next question')
    expect($transcript.get().nodes).toHaveLength(1)

    gateway.emit('message.complete', { status: 'ok' }, 'S1')
    await settle()
    expect($transcript.get().nodes.at(-1)).toMatchObject({
      text: '[Image #3] next question', images: [{ name: 'queued.png' }],
    })
  })

  it('clears pending previews with the session', async () => {
    await connect({ 'image.attach': { attached: true, id: 2 } })
    await createSession()
    await attachImage(new Blob(['image'], { type: 'image/png' }), 'shot.png')
    await clearSession()
    await submitPrompt('[Image #2] just a text reference')
    expect($transcript.get().nodes[0]).not.toHaveProperty('images')
  })

  it('does not carry an attachment into another session', async () => {
    const gateway = await connect({ 'image.attach': { attached: true, id: 2 } })
    await createSession()
    await attachImage(new Blob(['image'], { type: 'image/png' }), 'shot.png')
    gateway.results['session.create'] = { session_id: 'S2' }
    await createSession()
    await submitPrompt('[Image #2] just a text reference')
    expect($transcript.get().nodes[0]).not.toHaveProperty('images')
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
    $notice.set({ text: 'Previous session notice', tone: 'info' })

    gateway.results['session.create'] = { session_id: 'S2' }
    await createSession()
    await settle()

    expect($sessionId.get()).toBe('S2')
    expect($transcript.get().nodes).toEqual([])
    expect($notice.get().text).toBe('')
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

  it('says a switch was saved as the default for new sessions on the gateway’s word', async () => {
    // The gateway persists a pick to the user's settings (the TUI's and the
    // desktop's too) and echoes `persisted`; the notice must follow that
    // verdict rather than claim it for a switch the transport could not save.
    const gateway = await connect()

    await createSession()
    await settle()

    gateway.results['config.set'] = { ok: true, persisted: true, value: 'deepseek-flash' }
    await setModel('deepseek-flash', 'deepseek')
    await settle()

    expect(gateway.sent.find(frame => frame.method === 'config.set')?.params).toMatchObject({
      key: 'model',
      value: 'deepseek-flash --provider deepseek',
    })
    expect($notice.get()).toMatchObject({
      text: 'Set model to deepseek-flash and saved as your default for new sessions',
      tone: 'info',
    })

    gateway.results['config.set'] = { ok: true, persisted: false, value: 'deepseek-flash' }
    await setModel('deepseek-flash', 'deepseek')
    await settle()

    expect($notice.get().text).toBe('Set model to deepseek-flash for this session')

    // An older gateway that never says: claim neither.
    gateway.results['config.set'] = { ok: true, value: 'deepseek-flash' }
    await setModel('deepseek-flash', 'deepseek')
    await settle()

    expect($notice.get().text).toBe('Model: deepseek-flash')
  })

  it('words an effort change the same way', async () => {
    const gateway = await connect()

    await createSession()
    await settle()

    gateway.results['config.set'] = { ok: true, persisted: true, value: 'high' }
    await setEffort('high')
    await settle()

    expect(gateway.sent.find(frame => frame.method === 'config.set')?.params).toMatchObject({
      key: 'effort',
      value: 'high',
    })
    expect($notice.get().text).toBe('Set effort level to high and saved as your default for new sessions')

    gateway.results['config.set'] = { ok: true, persisted: false, value: 'auto' }
    await setEffort('auto')
    await settle()

    expect($notice.get().text).toBe('Set effort level to auto for this session')
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

describe('clearSession', () => {
  it('does not carry its confirmation into another conversation', async () => {
    const gateway = await connect()
    $sessionId.set('S1')

    await clearSession()
    await settle()

    expect($notice.get()).toMatchObject({ text: 'Conversation cleared.', tone: 'info' })

    gateway.results['session.resume'] = { session_id: 'S2', stored_session_id: 'stored-S2' }
    await resumeSession('stored-S2')
    await settle()

    expect($notice.get().text).toBe('')
  })

  it('ignores a late clear reply after navigating to another conversation', async () => {
    const gateway = await connect()
    $sessionId.set('S1')
    $transcript.set({
      ...emptyTranscript(),
      nodes: [{ at: 1, id: 'old', kind: 'user', text: 'old conversation' }],
    })
    gateway.hold('session.clear')

    const clearing = clearSession()
    await settle()

    gateway.results['session.resume'] = { session_id: 'S2', stored_session_id: 'stored-S2' }
    await resumeSession('stored-S2')
    await settle()
    $transcript.set({
      ...emptyTranscript(),
      nodes: [{ at: 2, id: 'new', kind: 'user', text: 'new conversation' }],
    })

    gateway.release('session.clear')
    await clearing
    await settle()

    expect($sessionId.get()).toBe('S2')
    expect($transcript.get().nodes).toMatchObject([
      { id: 'new', kind: 'user', text: 'new conversation' },
    ])
    expect($notice.get().text).toBe('')
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

describe('a new session and the previous one', () => {
  it('does not inherit the previous session model on New session', async () => {
    // The reported screen: a session running openai:gpt-5.6-luna, config
    // default deepseek, "New session" → the create carried the OLD session's
    // model explicitly (from $models, which mirrors whatever session the
    // picker last asked about) and overrode the default. A model is session
    // state; a create that names none lets the backend read the global
    // default into the new session's own config.
    const gateway = await connect()

    // The picker state after a turn on an openai session.
    $models.set({ model: 'gpt-5.6-luna', provider: 'openai', providers: [] })
    $sessionId.set('OLD')

    gateway.results['session.create'] = { session_id: 'S2' }
    await createSession()
    await settle()

    const create = gateway.sent.find(frame => frame.method === 'session.create')
    expect(create).toBeDefined()
    expect(create?.params).not.toHaveProperty('model')
    expect(create?.params).not.toHaveProperty('provider')
  })

  it('consumes a welcome-screen pick with the session it was for', async () => {
    // The pick rides exactly one create; the NEXT new session is back on the
    // global default rather than silently inheriting it forever.
    const gateway = await connect()

    await setModel('deepseek-v4-flash', 'deepseek')
    await createSession()
    await settle()

    gateway.results['session.create'] = { session_id: 'S2' }
    await createSession()
    await settle()

    const creates = gateway.sent.filter(frame => frame.method === 'session.create')
    expect(creates).toHaveLength(2)
    expect(creates[0]?.params).toMatchObject({ model: 'deepseek-v4-flash', provider: 'deepseek' })
    expect(creates[1]?.params).not.toHaveProperty('model')
  })
})

describe('the remembered session', () => {
  const MEMORY = 'clawcodex.web.session'

  it('remembers the session it is on, and lands back on it at the next boot', async () => {
    await connect()
    await submitPrompt('hello there')
    await settle()

    // `used`: a prompt went to it, so a reload will keep it rather than let
    // it go on the next navigation.
    expect(JSON.parse(window.localStorage.getItem(MEMORY) ?? 'null')).toEqual({
      live: 'S1',
      stored: 'S1',
      used: true,
    })

    // A reload: fresh stores, the same browser storage, the runtime still up.
    setGatewayClient(null)
    $sessionId.set(null)
    $transcript.set(emptyTranscript())

    const gateway = await connect({
      'session.resume': {
        messages: [{ content: [{ text: 'hello there', type: 'text' }], role: 'user' }],
        session_id: 'S1',
        stored_session_id: 'S1',
      },
    })

    const resume = gateway.sent.find(frame => frame.method === 'session.resume')

    expect(resume?.params).toMatchObject({ session_id: 'S1' })
    expect($sessionId.get()).toBe('S1')
    expect($transcript.get().nodes.length).toBeGreaterThan(0)
  })

  it('falls back to the row a resumed runtime came from when that runtime never saved', async () => {
    window.localStorage.setItem(MEMORY, JSON.stringify({ live: 'R', stored: 'X' }))

    // The first resume answers with a blank runtime, the second with the
    // stored row replayed.
    const client = new GatewayClient({
      connectTimeoutMs: 1000,
      requestTimeoutMs: 1000,
      socketFactory: () =>
        Object.assign(new FakeGateway(), {
          results: { ...DEFAULT_RESULTS },
          sequences: {
            'session.resume': [
              { messages: [], session_id: 'R2', stored_session_id: 'R' },
              {
                messages: [{ content: [{ text: 'hi', type: 'text' }], role: 'user' }],
                session_id: 'R3',
                stored_session_id: 'X',
              },
            ],
          },
        }) as unknown as WebSocket,
    })
    setGatewayClient(client)

    await start()
    await settle()

    const gateway = FakeGateway.current

    if (gateway === null) throw new Error('no socket was opened')

    const resumes = gateway.sent.filter(frame => frame.method === 'session.resume')

    // The runtime first, then — no record behind it — the stored row; the
    // blank runtime the first attempt spawned is closed.
    expect(resumes.map(frame => frame.params.session_id)).toEqual(['R', 'X'])
    expect(gateway.sent.find(frame => frame.method === 'session.close')?.params).toEqual({
      if_idle: true,
      session_id: 'R2',
    })
    expect($sessionId.get()).toBe('R3')
    expect($storedSessionId.get()).toBe('X')
    expect(JSON.parse(window.localStorage.getItem(MEMORY) ?? 'null')).toEqual({ live: 'R3', stored: 'X' })
  })

  it('keeps the sidebar on the row the conversation belongs to after re-attaching', async () => {
    window.localStorage.setItem(MEMORY, JSON.stringify({ live: 'R', stored: 'X' }))

    await connect({
      'session.resume': {
        messages: [{ content: [{ text: 'hi', type: 'text' }], role: 'user' }],
        session_id: 'R',
        stored_session_id: 'R',
      },
    })

    expect($sessionId.get()).toBe('R')
    expect($storedSessionId.get()).toBe('X')
    expect(JSON.parse(window.localStorage.getItem(MEMORY) ?? 'null')).toEqual({ live: 'R', stored: 'X' })
  })

  it('forgets a session the backend no longer knows, without a notice', async () => {
    window.localStorage.setItem(MEMORY, JSON.stringify({ live: 'gone', stored: 'gone' }))

    const client = new GatewayClient({
      connectTimeoutMs: 1000,
      requestTimeoutMs: 1000,
      socketFactory: () =>
        Object.assign(new FakeGateway(), {
          failing: new Set(['session.resume']),
          results: { ...DEFAULT_RESULTS },
        }) as unknown as WebSocket,
    })
    setGatewayClient(client)

    await start()
    await settle()

    expect($sessionId.get()).toBeNull()
    expect(window.localStorage.getItem(MEMORY)).toBeNull()
    expect($notice.get().text).toBe('')
  })
})

describe('opening a saved session', () => {
  const HISTORY = {
    found: true,
    info: { cwd: '/repo', model: 'stored-model' },
    messages: [{ content: [{ text: 'stored hello', type: 'text' }], role: 'user' }],
    session_id: 'X',
    stored_session_id: 'X',
    title: 'Saved chat',
  }

  it('renders the stored transcript before the runtime is attached', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    gateway.hold('session.resume')

    const opening = resumeSession('X')
    await settle()

    // The cold read landed: the row is highlighted, the transcript is up,
    // the title and workspace are the stored ones — and no runtime yet.
    expect($storedSessionId.get()).toBe('X')
    expect($transcript.get().nodes.map(node => node.kind)).toEqual(['user'])
    expect($sessionLoading.get()).toBe(false)
    expect($sessionAttaching.get()).toBe(true)
    expect($sessionId.get()).toBeNull()
    expect($workspace.get()).toBe('/repo')
    expect($sessionTitle.get()).toBe('Saved chat')

    gateway.release('session.resume')
    await opening
    await settle()

    expect($sessionId.get()).toBe('R1')
    expect($sessionAttaching.get()).toBe(false)
    expect($transcript.get().nodes).toHaveLength(1)

    const resume = gateway.sent.find(frame => frame.method === 'session.resume')
    expect(resume?.params).toMatchObject({ omit_messages: true, session_id: 'X' })
    expect(gateway.methods().indexOf('session.history')).toBeLessThan(gateway.methods().indexOf('session.resume'))
  })

  it('holds a prompt typed while the runtime attaches, for that conversation', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    gateway.hold('session.resume')
    void resumeSession('X')
    await settle()

    const sending = submitPrompt('and then?')
    await settle()

    // Nothing went out yet, and no session of its own was created.
    expect(gateway.methods()).not.toContain('prompt.submit')
    expect(gateway.methods()).not.toContain('session.create')

    gateway.release('session.resume')
    await sending
    await settle()

    const submit = gateway.sent.find(frame => frame.method === 'prompt.submit')
    expect(submit?.params).toEqual({ session_id: 'R1', text: 'and then?' })
  })

  it('falls back to the one-call resume on a backend without session.history', async () => {
    const gateway = await connect({
      'session.resume': {
        messages: HISTORY.messages,
        session_id: 'R1',
        stored_session_id: 'X',
        title: 'Saved chat',
      },
    })

    gateway.failing.add('session.history')
    // The fake refuses with "<method> refused"; the real backend says
    // "method not found: session.history".
    const socket = gateway as unknown as { send: (raw: string) => void }
    const send = socket.send.bind(gateway)
    socket.send = (raw: string) => {
      const frame = JSON.parse(raw) as { id: string; method: string }

      if (frame.method === 'session.history') {
        gateway.sent.push(frame as never)
        queueMicrotask(() => {
          ;(gateway as unknown as { deliver: (f: unknown) => void }).deliver({
            error: { message: 'method not found: session.history' },
            id: frame.id,
          })
        })

        return
      }

      send(raw)
    }

    await resumeSession('X')
    await settle()

    expect($sessionId.get()).toBe('R1')
    expect($transcript.get().nodes).toHaveLength(1)
    expect($sessionTitle.get()).toBe('Saved chat')
    expect($notice.get().text).toBe('')

    const resume = gateway.sent.find(frame => frame.method === 'session.resume')
    expect(resume?.params).not.toHaveProperty('omit_messages')
  })

  it('reports a row the backend cannot read, and does not attach to it', async () => {
    const gateway = await connect()

    gateway.failing.add('session.history')

    await resumeSession('gone')
    await settle()

    expect($notice.get().text).toContain('Could not open that session')
    expect(gateway.methods()).not.toContain('session.resume')
    expect($sessionLoading.get()).toBe(false)
  })

  it('does not reopen the conversation already on screen', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    await resumeSession('X')
    await settle()
    const before = gateway.sent.length

    await resumeSession('X')
    await settle()

    expect(gateway.sent.length).toBe(before)
  })

  it('releases a runtime it only looked at, and keeps one it used', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    // Looked at X, moved on to Y: X's runtime is let go — conditionally, on
    // the backend's own idle check.
    await resumeSession('X')
    await settle()
    expect($sessionId.get()).toBe('R1')

    gateway.results['session.history'] = { ...HISTORY, session_id: 'Y', stored_session_id: 'Y' }
    gateway.results['session.resume'] = { session_id: 'R2', stored_session_id: 'Y' }
    await resumeSession('Y')
    await settle()

    expect(gateway.sent.find(frame => frame.method === 'session.close')?.params).toEqual({
      if_idle: true,
      session_id: 'R1',
    })
    expect($sessionId.get()).toBe('R2')

    // A prompt was sent to Y: it is a session in use, loops and all, and
    // leaving it — even once the turn is over — must not close it.
    await submitPrompt('keep going')
    await settle()
    gateway.emit('message.complete', { status: 'ok', text: 'hi' }, 'R2')
    await settle()
    expect($transcript.get().running).toBe(false)

    gateway.results['session.history'] = { ...HISTORY, session_id: 'Z', stored_session_id: 'Z' }
    gateway.results['session.resume'] = { session_id: 'R3', stored_session_id: 'Z' }
    await resumeSession('Z')
    await settle()

    const closes = gateway.sent.filter(frame => frame.method === 'session.close')
    expect(closes.map(frame => frame.params.session_id)).toEqual(['R1'])
  })

  it('ignores a late attach after navigating on', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    gateway.hold('session.resume')
    void resumeSession('X')
    await settle()

    gateway.results['session.create'] = { session_id: 'S9' }
    await createSession()
    await settle()

    gateway.release('session.resume')
    await settle()

    expect($sessionId.get()).toBe('S9')
    expect($storedSessionId.get()).toBe('S9')
    expect($sessionAttaching.get()).toBe(false)
  })
})

describe('createSession options', () => {
  it('asks for the folder to be created and the session to be isolated', async () => {
    const gateway = await connect()

    gateway.results['session.create'] = { session_id: 'S2', worktree: { path: '/repo/.clawcodex/worktrees/x' } }
    const failure = await createSession({ createDir: true, cwd: '/new/place', worktree: true })
    await settle()

    expect(failure).toBeNull()
    expect(gateway.sent.find(frame => frame.method === 'session.create')?.params).toMatchObject({
      create_dir: true,
      cwd: '/new/place',
      worktree: true,
    })
    expect($sessionId.get()).toBe('S2')
  })

  it('hands the refusal back to the caller as well as the notice', async () => {
    const gateway = await connect()

    gateway.failing.add('session.create')
    const failure = await createSession({ cwd: 'relative' })

    expect(failure).toBe('session.create refused')
    expect($notice.get().text).toContain('session.create refused')
    expect($sessionId.get()).toBeNull()
  })
})

describe('a runtime that goes away', () => {
  const HISTORY = {
    found: true,
    info: { cwd: '/repo' },
    messages: [{ content: [{ text: 'stored hello', type: 'text' }], role: 'user' }],
    session_id: 'X',
    stored_session_id: 'X',
  }

  it('reconnects the conversation and resends when the backend no longer has the runtime', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    await resumeSession('X')
    await settle()
    expect($sessionId.get()).toBe('R1')

    // The runtime died behind a restart; the next attach lands on a new one.
    gateway.sequences['prompt.submit'] = []
    gateway.results['session.resume'] = { session_id: 'R2', stored_session_id: 'X' }
    const socket = gateway as unknown as { send: (raw: string) => void; deliver: (f: unknown) => void }
    const send = socket.send.bind(gateway)
    let refusals = 0
    socket.send = (raw: string) => {
      const frame = JSON.parse(raw) as { id: string; method: string; params: { session_id?: string } }

      if (frame.method === 'prompt.submit' && frame.params.session_id === 'R1') {
        refusals += 1
        gateway.sent.push(frame as never)
        queueMicrotask(() => {
          socket.deliver({ error: { message: 'unknown session: R1' }, id: frame.id })
        })

        return
      }

      send(raw)
    }

    await submitPrompt('still there?')
    await settle()

    const submits = gateway.sent.filter(frame => frame.method === 'prompt.submit')
    expect(refusals).toBe(1)
    expect(submits.map(frame => frame.params.session_id)).toEqual(['R1', 'R2'])
    expect($sessionId.get()).toBe('R2')
    expect($notice.get().text).toBe('')
    expect($transcript.get().nodes.map(node => node.kind)).toEqual(['user', 'user'])

    // The reconnected runtime was used: moving on does not close it.
    gateway.results['session.history'] = { ...HISTORY, session_id: 'Y', stored_session_id: 'Y' }
    gateway.results['session.resume'] = { session_id: 'R3', stored_session_id: 'Y' }
    await resumeSession('Y')
    await settle()
    expect(gateway.methods()).not.toContain('session.close')
  })

  it('drops the runtime id on session.closed and reconnects on the next prompt', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    await resumeSession('X')
    await settle()

    gateway.emit('session.closed', {}, 'R1')
    await settle()
    expect($sessionId.get()).toBeNull()
    expect($storedSessionId.get()).toBe('X')
    expect($transcript.get().nodes).toHaveLength(1)

    gateway.results['session.resume'] = { session_id: 'R2', stored_session_id: 'X' }
    await submitPrompt('back again')
    await settle()

    expect(gateway.methods()).not.toContain('session.create')
    expect(gateway.sent.find(frame => frame.method === 'prompt.submit')?.params).toEqual({
      session_id: 'R2',
      text: 'back again',
    })
  })

  it('adopts a runtime handed back mid-turn as running', async () => {
    await connect({
      'session.history': HISTORY,
      'session.resume': { info: { running: true }, session_id: 'R1', stored_session_id: 'X' },
    })

    await resumeSession('X')
    await settle()

    expect($transcript.get().running).toBe(true)
  })

  it('opens row B while row A is still attaching, and lets A’s late attach go', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    gateway.hold('session.resume')
    void resumeSession('X')
    await settle()

    gateway.results['session.history'] = { ...HISTORY, session_id: 'Y', stored_session_id: 'Y', title: 'Row B' }
    gateway.results['session.resume'] = { session_id: 'R2', stored_session_id: 'Y' }
    const opening = resumeSession('Y')
    await settle()
    expect($storedSessionId.get()).toBe('Y')
    expect($sessionTitle.get()).toBe('Row B')

    gateway.release('session.resume')
    await opening
    await settle()

    expect($sessionId.get()).toBe('R2')
    expect($storedSessionId.get()).toBe('Y')
    expect($sessionAttaching.get()).toBe(false)
    // A's late attach produced a runtime nobody is on: let go of it, on the
    // backend's idle check.
    expect(gateway.sent.filter(frame => frame.method === 'session.close').map(frame => frame.params)).toEqual([
      { if_idle: true, session_id: 'R1' },
    ])
  })

  it('does not carry "used" onto a fresh replay after a reload', async () => {
    const MEMORY = 'clawcodex.web.session'
    window.localStorage.setItem(MEMORY, JSON.stringify({ live: 'gone', stored: 'X', used: true }))

    // The used runtime is gone; the reload lands on a fresh replay of X.
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R9', stored_session_id: 'X' },
    })
    expect($sessionId.get()).toBe('R9')
    expect(JSON.parse(window.localStorage.getItem(MEMORY) ?? 'null')).toEqual({ live: 'R9', stored: 'X' })

    gateway.results['session.history'] = { ...HISTORY, session_id: 'Y', stored_session_id: 'Y' }
    gateway.results['session.resume'] = { session_id: 'R10', stored_session_id: 'Y' }
    await resumeSession('Y')
    await settle()

    expect(gateway.sent.find(frame => frame.method === 'session.close')?.params).toEqual({
      if_idle: true,
      session_id: 'R9',
    })
  })

  it('keeps a session that was in use before a reload', async () => {
    await connect()
    await submitPrompt('hello there')
    await settle()

    const MEMORY = 'clawcodex.web.session'
    expect(JSON.parse(window.localStorage.getItem(MEMORY) ?? 'null')).toEqual({
      live: 'S1',
      stored: 'S1',
      used: true,
    })

    setGatewayClient(null)
    $sessionId.set(null)
    $transcript.set(emptyTranscript())

    const gateway = await connect({
      'session.history': { found: true, messages: HISTORY.messages, session_id: 'S1', stored_session_id: 'S1' },
      'session.resume': { session_id: 'S1', stored_session_id: 'S1' },
    })
    expect($sessionId.get()).toBe('S1')

    // Moving on does not close it: it was used, and a reload changes nothing.
    gateway.results['session.history'] = HISTORY
    gateway.results['session.resume'] = { session_id: 'R1', stored_session_id: 'X' }
    await resumeSession('X')
    await settle()

    expect(gateway.methods()).not.toContain('session.close')
  })
})

describe('createSession and the conversation on screen', () => {
  const HISTORY = {
    found: true,
    messages: [{ content: [{ text: 'stored hello', type: 'text' }], role: 'user' }],
    session_id: 'X',
    stored_session_id: 'X',
    title: 'Saved chat',
  }

  it('keeps the conversation when the backend refuses the create', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    await resumeSession('X')
    await settle()

    gateway.failing.add('session.create')
    const failure = await createSession({ cwd: 'relative' })
    await settle()

    expect(failure).toBe('session.create refused')
    expect($sessionId.get()).toBe('R1')
    expect($storedSessionId.get()).toBe('X')
    expect($transcript.get().nodes).toHaveLength(1)
    expect($sessionTitle.get()).toBe('Saved chat')
    expect(gateway.methods()).not.toContain('session.close')
  })

  it('lets a slow create go when a row was opened meanwhile', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'R1', stored_session_id: 'X' },
    })

    gateway.hold('session.create')
    gateway.results['session.create'] = { session_id: 'S9' }
    const creating = createSession({ cwd: '/repo', worktree: true })
    await settle()

    await resumeSession('X')
    await settle()
    expect($sessionId.get()).toBe('R1')

    gateway.release('session.create')
    expect(await creating).toBeNull()
    await settle()

    // The row stays on screen; the runtime the create spawned is released.
    expect($sessionId.get()).toBe('R1')
    expect($storedSessionId.get()).toBe('X')
    expect($transcript.get().nodes).toHaveLength(1)
    expect(gateway.sent.find(frame => frame.method === 'session.close')?.params).toEqual({
      if_idle: true,
      session_id: 'S9',
    })
  })
})

describe('the same row opened again while its first open is still landing', () => {
  const HISTORY = {
    found: true,
    messages: [{ content: [{ text: 'stored hello', type: 'text' }], role: 'user' }],
    session_id: 'A',
    stored_session_id: 'A',
  }

  it('does not close the runtime the later open adopted (A, B, A)', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'RA', stored_session_id: 'A' },
    })

    gateway.hold('session.resume')
    void resumeSession('A')
    await settle()

    gateway.results['session.history'] = { ...HISTORY, session_id: 'B', stored_session_id: 'B' }
    gateway.results['session.resume'] = { session_id: 'RB', stored_session_id: 'B' }
    void resumeSession('B')
    await settle()

    gateway.results['session.history'] = HISTORY
    gateway.results['session.resume'] = { session_id: 'RA', stored_session_id: 'A' }
    const third = resumeSession('A')
    await settle()

    // All three attaches land, in order: A (stale), B (stale), A (current).
    gateway.release('session.resume')
    await third
    await settle()

    expect($sessionId.get()).toBe('RA')
    expect($storedSessionId.get()).toBe('A')
    // Only B's runtime is let go; A's is the one on screen.
    expect(gateway.sent.filter(frame => frame.method === 'session.close').map(frame => frame.params)).toEqual([
      { if_idle: true, session_id: 'RB' },
    ])
  })

  it('leaves the prompt with the reader when a saved row cannot be reconnected', async () => {
    const gateway = await connect({
      'session.history': HISTORY,
      'session.resume': { session_id: 'RA', stored_session_id: 'A' },
    })

    await resumeSession('A')
    await settle()
    gateway.emit('session.closed', {}, 'RA')
    await settle()
    expect($sessionId.get()).toBeNull()

    gateway.failing.add('session.resume')
    await submitPrompt('hello?')
    await settle()

    expect(gateway.methods()).not.toContain('session.create')
    expect(gateway.methods()).not.toContain('prompt.submit')
    expect($notice.get().text).toContain('Could not resume that session')
  })
})

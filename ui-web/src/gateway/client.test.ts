import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { GatewayClient } from './client.ts'
import type { GatewayEvent } from './protocol.ts'

/** A WebSocket stand-in with hand-driven open/message/close. */
class FakeSocket {
  static instances: FakeSocket[] = []

  readyState = 0
  sent: string[] = []
  closed = false

  private readonly listeners = new Map<string, Set<(event: unknown) => void>>()

  constructor(public url: string) {
    FakeSocket.instances.push(this)
  }

  addEventListener(type: string, handler: (event: unknown) => void): void {
    const set = this.listeners.get(type) ?? new Set()
    set.add(handler)
    this.listeners.set(type, set)
  }

  removeEventListener(type: string, handler: (event: unknown) => void): void {
    this.listeners.get(type)?.delete(handler)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.closed = true
    this.readyState = 3
  }

  emit(type: string, event: unknown = {}): void {
    for (const handler of [...(this.listeners.get(type) ?? [])]) handler(event)
  }

  open(): void {
    this.readyState = 1
    this.emit('open')
  }

  receive(frame: unknown): void {
    this.emit('message', { data: JSON.stringify(frame) })
  }

  drop(): void {
    this.readyState = 3
    this.emit('close', {})
  }

  lastRequest(): { id: string; method: string; params: Record<string, unknown> } {
    const raw = this.sent.at(-1)

    if (raw === undefined) throw new Error('nothing sent')

    return JSON.parse(raw) as { id: string; method: string; params: Record<string, unknown> }
  }
}

/**
 * Every client is closed in afterEach. A live one keeps its retry loop running
 * across test boundaries and opens sockets into the *next* test's instance
 * list — which is the reconnect behaviour working, and a test leak.
 */
const clients: GatewayClient[] = []

function newClient(overrides = {}) {
  const client = new GatewayClient({
    backoffMs: [1],
    connectTimeoutMs: 50,
    requestTimeoutMs: 50,
    socketFactory: url => new FakeSocket(url) as unknown as WebSocket,
    ...overrides,
  })
  clients.push(client)

  return client
}

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', { CLOSED: 3, CLOSING: 2, CONNECTING: 0, OPEN: 1 })
})

afterEach(() => {
  for (const client of clients.splice(0)) client.close()

  vi.unstubAllGlobals()
})

describe('connect', () => {
  it('refuses anything that is not a ws URL', async () => {
    await expect(newClient().connect('https://example.com')).rejects.toThrow(/ws:\/\//)
    await expect(newClient().connect('' as string)).rejects.toThrow(/ws:\/\//)
  })

  it('resolves on the open handshake and reports state', async () => {
    const client = newClient()
    const states: string[] = []
    client.onState(state => states.push(state))

    const connecting = client.connect('ws://127.0.0.1:1/api/ws?token=t')
    FakeSocket.instances[0]?.open()
    await connecting

    expect(client.connectionState).toBe('open')
    expect(states).toEqual(['idle', 'connecting', 'open'])
  })

  it('fails to error when the handshake never lands', async () => {
    vi.useFakeTimers()
    const client = newClient()

    const connecting = client.connect('ws://127.0.0.1:1/api/ws')
    const rejected = expect(connecting).rejects.toThrow(/timed out/)
    await vi.advanceTimersByTimeAsync(60)
    await rejected

    expect(client.connectionState).toBe('error')
    // The half-open socket is dropped so a retry starts clean.
    expect(FakeSocket.instances[0]?.closed).toBe(true)
    vi.useRealTimers()
  })
})

describe('requests', () => {
  it('round-trips a result by id', async () => {
    const client = newClient()
    const connecting = client.connect('ws://x/api/ws')
    const socket = FakeSocket.instances[0]!
    socket.open()
    await connecting

    const pending = client.request<{ ok: boolean }>('session.create', { cwd: '/w' })
    const frame = socket.lastRequest()

    expect(frame.method).toBe('session.create')
    expect(frame.params).toEqual({ cwd: '/w' })

    socket.receive({ id: frame.id, result: { ok: true } })
    await expect(pending).resolves.toEqual({ ok: true })
  })

  it('surfaces a server error as a rejection', async () => {
    const client = newClient()
    const connecting = client.connect('ws://x/api/ws')
    const socket = FakeSocket.instances[0]!
    socket.open()
    await connecting

    const pending = client.request('nope')
    socket.receive({ error: { message: 'method not found: nope' }, id: socket.lastRequest().id })

    await expect(pending).rejects.toThrow('method not found: nope')
  })

  it('rejects when the socket is not open', async () => {
    await expect(newClient().request('anything')).rejects.toThrow('gateway not connected')
  })

  it('rejects everything in flight when the socket drops', async () => {
    const client = newClient()
    const connecting = client.connect('ws://x/api/ws')
    const socket = FakeSocket.instances[0]!
    socket.open()
    await connecting

    const pending = client.request('slow')
    socket.drop()

    await expect(pending).rejects.toThrow(/closed/)
  })
})

describe('events', () => {
  it('dispatches a push to its type handler and to onAny', async () => {
    const client = newClient()
    const connecting = client.connect('ws://x/api/ws')
    const socket = FakeSocket.instances[0]!
    socket.open()
    await connecting

    const typed: GatewayEvent[] = []
    const all: GatewayEvent[] = []
    client.on('message.delta', event => typed.push(event))
    client.onAny(event => all.push(event))

    socket.receive({ method: 'event', params: { payload: { text: 'hi' }, type: 'message.delta' } })

    expect(typed).toHaveLength(1)
    expect(all).toHaveLength(1)
    expect(typed[0]?.payload).toEqual({ text: 'hi' })
  })

  it('ignores a push that carries an id', async () => {
    // A pushed id would be swallowed by the pending-call map; the server never
    // sends one, and the client must not invent a code path for it.
    const client = newClient()
    const connecting = client.connect('ws://x/api/ws')
    const socket = FakeSocket.instances[0]!
    socket.open()
    await connecting

    const seen: GatewayEvent[] = []
    client.onAny(event => seen.push(event))
    socket.receive({ id: 'r99', method: 'event', params: { type: 'message.delta' } })

    expect(seen).toHaveLength(0)
  })

  it('survives a non-JSON frame', async () => {
    const client = newClient()
    const connecting = client.connect('ws://x/api/ws')
    const socket = FakeSocket.instances[0]!
    socket.open()
    await connecting

    expect(() => socket.emit('message', { data: 'not json' })).not.toThrow()
    expect(client.connectionState).toBe('open')
  })

  it('unsubscribes cleanly', async () => {
    const client = newClient()
    const connecting = client.connect('ws://x/api/ws')
    const socket = FakeSocket.instances[0]!
    socket.open()
    await connecting

    const seen: GatewayEvent[] = []
    const off = client.on('error', event => seen.push(event))
    off()
    socket.receive({ method: 'event', params: { type: 'error' } })

    expect(seen).toHaveLength(0)
  })
})

describe('reconnect', () => {
  it('reopens after a drop', async () => {
    vi.useFakeTimers()
    const client = newClient()
    const connecting = client.connect('ws://x/api/ws')
    FakeSocket.instances[0]?.open()
    await connecting

    FakeSocket.instances[0]?.drop()
    expect(client.connectionState).toBe('reconnecting')

    await vi.advanceTimersByTimeAsync(5)
    expect(FakeSocket.instances).toHaveLength(2)

    FakeSocket.instances[1]?.open()
    await vi.advanceTimersByTimeAsync(1)
    expect(client.connectionState).toBe('open')
    vi.useRealTimers()
  })

  it('stops retrying after close()', async () => {
    vi.useFakeTimers()
    const client = newClient()
    const connecting = client.connect('ws://x/api/ws')
    FakeSocket.instances[0]?.open()
    await connecting

    client.close()
    await vi.advanceTimersByTimeAsync(50)

    expect(FakeSocket.instances).toHaveLength(1)
    expect(client.connectionState).toBe('closed')
    vi.useRealTimers()
  })
})

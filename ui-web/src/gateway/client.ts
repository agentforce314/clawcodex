/**
 * JSON-RPC client for the ClawCodex gateway socket.
 *
 * Adapted from the desktop shell's `@clawcodex/shared` gateway client so both
 * surfaces speak the transport identically — the divergences here are the
 * browser's: an automatic reconnect with backoff (a desktop renderer is
 * restarted by its shell; a tab is not) and no Node/Electron seams.
 *
 * Frame handling rule: a frame with an `id` resolves a pending call, a frame
 * with `method === 'event'` is a push. Nothing else is dispatched.
 */

import type { GatewayEvent, GatewayEventType } from './protocol.ts'

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed' | 'error'

type PendingCall = {
  reject: (error: Error) => void
  resolve: (value: unknown) => void
  timer?: ReturnType<typeof setTimeout>
}

interface JsonRpcFrame {
  error?: { message?: string }
  id?: number | string | null
  method?: string
  params?: GatewayEvent
  result?: unknown
}

export interface GatewayClientOptions {
  /** Injected in tests; defaults to the platform WebSocket. */
  socketFactory?: (url: string) => WebSocket
  requestTimeoutMs?: number
  connectTimeoutMs?: number
  /** Reconnect backoff steps in ms; the last value repeats. */
  backoffMs?: readonly number[]
}

const ANY = '*'
const DEFAULT_REQUEST_TIMEOUT_MS = 120_000
// A reconnect after sleep/wake must not hang forever in 'connecting' — that
// state keeps the composer disabled with no way out. Fail to 'error' instead
// so the retry loop can start over.
const DEFAULT_CONNECT_TIMEOUT_MS = 15_000
const DEFAULT_BACKOFF_MS = [250, 500, 1000, 2000, 4000, 8000] as const

export class GatewayClient {
  private nextId = 0
  private pending = new Map<number | string, PendingCall>()
  private socket: WebSocket | null = null
  private state: ConnectionState = 'idle'
  private url: string | null = null
  private retries = 0
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private disposed = false
  private readonly eventHandlers = new Map<string, Set<(event: GatewayEvent) => void>>()
  private readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  private readonly options: Required<Omit<GatewayClientOptions, 'socketFactory'>> &
    Pick<GatewayClientOptions, 'socketFactory'>

  constructor(options: GatewayClientOptions = {}) {
    this.options = {
      backoffMs: options.backoffMs ?? DEFAULT_BACKOFF_MS,
      connectTimeoutMs: options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS,
      requestTimeoutMs: options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
      socketFactory: options.socketFactory,
    }
  }

  get connectionState(): ConnectionState {
    return this.state
  }

  /**
   * Open the socket and keep it open. Resolves on the first successful
   * handshake; later drops are handled by the internal retry loop, so callers
   * subscribe to `onState` rather than re-calling this.
   */
  async connect(url: string): Promise<void> {
    if (!/^wss?:\/\//.test(url)) {
      throw new Error(`gateway connect() needs a ws:// or wss:// URL, got ${JSON.stringify(url)}`)
    }

    this.url = url
    this.disposed = false

    if (this.socket?.readyState === WebSocket.OPEN) return
    if (this.state === 'connecting' || this.state === 'reconnecting') return

    await this.open()
  }

  /** Close for good: no reconnect, pending calls rejected. */
  close(): void {
    this.disposed = true

    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }

    const socket = this.socket
    this.socket = null

    try {
      socket?.close()
    } finally {
      this.setState('closed')
      this.rejectAllPending(new Error('gateway closed'))
    }
  }

  on<P = unknown>(
    type: GatewayEventType,
    handler: (event: GatewayEvent<P>) => void,
  ): () => void {
    let handlers = this.eventHandlers.get(type)

    if (!handlers) {
      handlers = new Set()
      this.eventHandlers.set(type, handlers)
    }

    handlers.add(handler as (event: GatewayEvent) => void)

    return () => {
      handlers.delete(handler as (event: GatewayEvent) => void)
    }
  }

  onAny(handler: (event: GatewayEvent) => void): () => void {
    return this.on(ANY as GatewayEventType, handler)
  }

  onState(handler: (state: ConnectionState) => void): () => void {
    this.stateHandlers.add(handler)
    handler(this.state)

    return () => {
      this.stateHandlers.delete(handler)
    }
  }

  request<T>(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = this.options.requestTimeoutMs,
  ): Promise<T> {
    const socket = this.socket

    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('gateway not connected'))
    }

    const id = `r${++this.nextId}`

    return new Promise<T>((resolve, reject) => {
      const pending: PendingCall = {
        resolve: value => {
          resolve(value as T)
        },
        reject,
      }

      if (timeoutMs > 0) {
        pending.timer = setTimeout(() => {
          if (this.pending.delete(id)) {
            reject(new Error(`request timed out after ${Math.round(timeoutMs / 1000)}s: ${method}`))
          }
        }, timeoutMs)
      }

      this.pending.set(id, pending)

      try {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }))
      } catch (error) {
        this.clearPending(id)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  /* ── internals ─────────────────────────────────────────────────────────── */

  private async open(): Promise<void> {
    const url = this.url

    if (url === null) return

    this.setState(this.retries === 0 ? 'connecting' : 'reconnecting')

    const socket = this.options.socketFactory?.(url) ?? new WebSocket(url)
    this.socket = socket

    socket.addEventListener('message', message => {
      if (this.socket !== socket) return
      this.handleMessage(message.data)
    })

    socket.addEventListener('close', () => {
      if (this.socket !== socket) return
      this.socket = null
      this.rejectAllPending(new Error('gateway socket closed'))
      this.scheduleRetry()
    })

    await new Promise<void>((resolve, reject) => {
      let settled = false
      let timer: ReturnType<typeof setTimeout> | undefined

      const cleanup = () => {
        if (timer !== undefined) clearTimeout(timer)
        socket.removeEventListener('open', onOpen)
        socket.removeEventListener('error', onError)
      }

      const onOpen = () => {
        if (settled || this.socket !== socket) return
        settled = true
        cleanup()
        this.retries = 0
        this.setState('open')
        resolve()
      }

      const onError = () => {
        if (settled || this.socket !== socket) return
        settled = true
        cleanup()
        this.setState('error')
        reject(new Error('gateway connection failed'))
      }

      socket.addEventListener('open', onOpen, { once: true })
      socket.addEventListener('error', onError, { once: true })

      if (this.options.connectTimeoutMs > 0) {
        timer = setTimeout(() => {
          if (settled) return
          settled = true
          cleanup()

          // Drop the half-open socket so the next attempt starts clean rather
          // than short-circuiting on a zombie 'connecting' state.
          if (this.socket === socket) {
            try {
              socket.close()
            } catch {
              /* already gone */
            }
            this.socket = null
          }

          this.setState('error')
          reject(new Error('gateway connection timed out'))
        }, this.options.connectTimeoutMs)
      }
    })
  }

  private scheduleRetry(): void {
    if (this.disposed || this.url === null || this.retryTimer !== null) {
      if (this.disposed) this.setState('closed')
      return
    }

    const steps = this.options.backoffMs
    const delay = steps[Math.min(this.retries, steps.length - 1)] ?? 1000
    this.retries += 1
    this.setState('reconnecting')

    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      void this.open().catch(() => {
        // open() already moved the state to 'error'; the close handler that
        // follows (or this catch, when the socket never opened) re-arms.
        this.scheduleRetry()
      })
    }, delay)
  }

  private handleMessage(raw: unknown): void {
    let frame: JsonRpcFrame

    try {
      frame = JSON.parse(typeof raw === 'string' ? raw : String(raw)) as JsonRpcFrame
    } catch {
      return
    }

    if (frame.id !== undefined && frame.id !== null) {
      const call = this.pending.get(frame.id)

      if (!call) return

      this.clearPending(frame.id)

      if (frame.error) call.reject(new Error(frame.error.message || 'ClawCodex RPC failed'))
      else call.resolve(frame.result)

      return
    }

    if (frame.method === 'event' && frame.params?.type) {
      this.dispatchEvent(frame.params)
    }
  }

  private clearPending(id: number | string): void {
    const call = this.pending.get(id)

    if (call?.timer) clearTimeout(call.timer)

    this.pending.delete(id)
  }

  private dispatchEvent(event: GatewayEvent): void {
    for (const handler of this.eventHandlers.get(event.type) ?? []) handler(event)
    for (const handler of this.eventHandlers.get(ANY) ?? []) handler(event)
  }

  private rejectAllPending(error: Error): void {
    for (const [id, call] of this.pending) {
      if (call.timer) clearTimeout(call.timer)
      call.reject(error)
      this.pending.delete(id)
    }
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) return

    this.state = state

    for (const handler of this.stateHandlers) handler(state)
  }
}

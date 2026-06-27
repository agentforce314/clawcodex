/**
 * Pluggable transport for the Direct Connect client. The agent protocol is
 * NDJSON and transport-agnostic — only the framing differs:
 *   - StdioTransport (default, local): spawns the agent-server child and talks
 *     over its stdin/stdout pipes. A pipe can't idle-time-out, so the session
 *     never silently disconnects (unlike a WebSocket).
 *   - WsTransport (attach): connects to an already-running / remote agent-server
 *     over WebSocket.
 * This mirrors hermes-agent: stdio for the local child, WS only as a remote
 * sidecar.
 */
import { type ChildProcess, spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import WebSocket from 'ws'

export interface TransportHandlers {
  onData: (text: string) => void
  onOpen?: () => void
  onClose: () => void
  onError: (err: Error) => void
}

export interface Transport {
  /** Open the link and start delivering frames to the handlers. */
  start(h: TransportHandlers): Promise<void>
  /** Send one already-`\n`-terminated NDJSON frame. */
  send(text: string): void
  /** Tear the link down (kills the child for stdio; closes the socket for WS). */
  close(): void
}

// ── WebSocket — attach to an already-running / remote agent-server ───────────
export class WsTransport implements Transport {
  private ws: WebSocket | undefined

  constructor(
    private readonly wsUrl: string,
    private readonly authToken?: string,
  ) {}

  start(h: TransportHandlers): Promise<void> {
    return new Promise((resolve, reject) => {
      const headers: Record<string, string> = {}
      if (this.authToken) headers['authorization'] = `Bearer ${this.authToken}`
      const ws = new WebSocket(this.wsUrl, { headers })
      this.ws = ws
      ws.on('open', () => {
        h.onOpen?.()
        resolve()
      })
      ws.on('message', (data: WebSocket.RawData) => h.onData(data.toString('utf8')))
      ws.on('close', () => h.onClose())
      ws.on('error', (err: Error) => {
        h.onError(err)
        reject(err)
      })
    })
  }

  send(text: string): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(text)
  }

  close(): void {
    this.ws?.close()
  }
}

// ── stdio — spawn + own the agent-server child (the default local link) ──────
const STDERR_RING = 50

export class StdioTransport implements Transport {
  private child: ChildProcess | undefined
  private readonly errLog: string[] = []

  constructor(
    private readonly cmd: string,
    private readonly args: string[],
    private readonly opts: { cwd: string; env?: NodeJS.ProcessEnv },
  ) {}

  start(h: TransportHandlers): Promise<void> {
    return new Promise((resolve) => {
      const child = spawn(this.cmd, this.args, {
        cwd: this.opts.cwd,
        stdio: ['pipe', 'pipe', 'pipe'],
        // PYTHONUNBUFFERED so the child's stdout frames aren't block-buffered.
        env: { ...process.env, ...this.opts.env, PYTHONUNBUFFERED: '1' },
      })
      this.child = child

      const out = createInterface({ input: child.stdout! })
      out.on('line', (line) => {
        if (line.trim()) h.onData(line)
      })
      const err = createInterface({ input: child.stderr! })
      err.on('line', (line) => {
        this.errLog.push(line)
        if (this.errLog.length > STDERR_RING) this.errLog.shift()
      })

      child.on('error', (e) => h.onError(e))
      child.on('exit', () => h.onClose())

      // A pipe is "open" immediately; the child signals readiness with its
      // system/init frame (the app shows "starting…" until then).
      h.onOpen?.()
      resolve()
    })
  }

  send(text: string): void {
    if (this.child?.stdin?.writable) this.child.stdin.write(text)
  }

  close(): void {
    try {
      if (this.child && !this.child.killed) this.child.kill()
    } catch {
      // best effort
    }
  }

  /** Recent child stderr (diagnostics) — for surfacing spawn/startup failures. */
  stderrTail(): string {
    return this.errLog.join('\n')
  }
}

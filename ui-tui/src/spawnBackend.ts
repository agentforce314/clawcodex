/**
 * Spawn + own the Python agent-server as a child of this TUI — the hermes-agent
 * route: the TypeScript client is the durable parent; the Python backend is a
 * disposable child it launches, reads a `cc://` URL from, and tears down on
 * exit. (Mirrors hermes's GatewayClient spawning `python -m tui_gateway.entry`,
 * but over the Direct Connect HTTP+WS transport rather than stdio.)
 *
 * The command to run is taken from `CLAWCODEX_AGENT_SERVER_CMD` (set by the
 * `clawcodex tui` launcher, which knows the right interpreter), falling back to
 * `clawcodex agent-server` on PATH. We append `--host 127.0.0.1 --port 0
 * --token <random>` so the server binds an ephemeral loopback port and requires
 * our per-launch token; we then read its announced `cc://host:port` line.
 */
import { type ChildProcess, spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { createInterface } from 'node:readline'

export interface SpawnedBackend {
  ccUrl: string
  token: string
  child: ChildProcess
  dispose: () => void
}

// The Python agent stack imports slowly on a cold start (~20s observed), so the
// default is generous; override with CLAWCODEX_TUI_STARTUP_TIMEOUT_MS.
const STARTUP_TIMEOUT_MS = Math.max(
  5000,
  parseInt(process.env.CLAWCODEX_TUI_STARTUP_TIMEOUT_MS ?? '60000', 10) || 60000,
)

function resolveCmd(): string[] {
  const raw = process.env.CLAWCODEX_AGENT_SERVER_CMD?.trim()
  if (raw) return raw.split(/\s+/)
  return ['clawcodex', 'agent-server']
}

/** Last N lines of the child's stderr, kept for diagnosing a failed startup. */
class Ring {
  private lines: string[] = []
  constructor(private max = 40) {}
  push(line: string) {
    this.lines.push(line)
    if (this.lines.length > this.max) this.lines.shift()
  }
  tail(): string {
    return this.lines.join('\n')
  }
}

export function spawnBackend(opts: { cwd: string }): Promise<SpawnedBackend> {
  const token = randomBytes(24).toString('base64url')
  const [cmd, ...base] = resolveCmd()
  // `--exit-on-parent`: the child exits when its stdin (the pipe we hold open)
  // hits EOF, so it dies even if we crash without running cleanup.
  const args = [...base, '--host', '127.0.0.1', '--port', '0', '--token', token, '--exit-on-parent']

  const child = spawn(cmd!, args, {
    cwd: opts.cwd,
    // stdin is a pipe we keep open for its whole life — when this process dies,
    // the OS closes it, the child sees EOF, and exits (the hermes route).
    stdio: ['pipe', 'pipe', 'pipe'],
    // PYTHONUNBUFFERED: Python block-buffers stdout to a pipe (not a TTY), so
    // without this the `cc://` announcement sits unflushed and we never see it.
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  })

  const dispose = () => {
    try {
      if (!child.killed) child.kill()
    } catch {
      // best effort
    }
  }

  return new Promise<SpawnedBackend>((resolve, reject) => {
    let settled = false
    const errLog = new Ring()

    const finish = (fn: () => void) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      fn()
    }

    const timer = setTimeout(() => {
      finish(() => {
        dispose()
        reject(new Error(`agent-server did not start within ${STARTUP_TIMEOUT_MS}ms\n${errLog.tail()}`))
      })
    }, STARTUP_TIMEOUT_MS)

    // stdout: scan for the announced cc:// URL, then keep draining so the
    // child never blocks on a full pipe.
    const out = createInterface({ input: child.stdout! })
    out.on('line', (line) => {
      const m = line.match(/cc:\/\/(\S+)/)
      if (m) {
        finish(() =>
          resolve({ ccUrl: `cc://${m[1]}`, token, child, dispose }),
        )
      }
    })

    // stderr: buffer (never write to the terminal — it would corrupt the Ink
    // render once we're up); surface it only if startup fails.
    const err = createInterface({ input: child.stderr! })
    err.on('line', (line) => errLog.push(line))

    child.on('error', (e) => {
      finish(() => reject(new Error(`failed to spawn agent-server (${cmd}): ${e.message}`)))
    })
    child.on('exit', (code) => {
      finish(() =>
        reject(new Error(`agent-server exited (code ${code ?? 'null'}) before announcing a cc:// URL\n${errLog.tail()}`)),
      )
    })
  })
}

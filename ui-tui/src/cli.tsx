#!/usr/bin/env node
/**
 * `clawcodex-tui` — the TypeScript Ink TUI.
 *
 * Two modes:
 *   - spawn (default): the TUI is the parent — it spawns the Python
 *     agent-server as a child it owns (the hermes-agent route) and talks over
 *     the child's stdin/stdout (NDJSON). A pipe can't idle-time-out, so the
 *     session never silently disconnects.
 *   - attach: pass a `cc://`/`http://` URL to connect to an already-running
 *     `clawcodex agent-server` (e.g. a remote one) over WebSocket.
 *
 * Usage:
 *   clawcodex-tui [--cwd DIR]                                # spawn + own a backend
 *   clawcodex-tui <cc://host:port> [--token T] [--cwd DIR]   # attach
 */
import { render } from 'ink'
import React from 'react'
import { App } from './App.js'
import { createSession } from './client.js'
import { StdioTransport, WsTransport, type Transport } from './transport.js'

interface Args {
  url: string | undefined
  token: string | undefined
  cwd: string
}

function parseArgs(argv: string[]): Args {
  let url: string | undefined
  let token: string | undefined = process.env['CLAWCODEX_TUI_TOKEN']
  let cwd = process.cwd()
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i] as string
    if (a === '--token') {
      token = argv[++i]
    } else if (a === '--cwd') {
      cwd = argv[++i] ?? cwd
    } else if (!a.startsWith('-')) {
      url = a
    }
  }
  return { url, token, cwd }
}

function toHttpUrl(url: string): string {
  if (url.startsWith('cc://')) return 'http://' + url.slice('cc://'.length)
  if (url.startsWith('cc+unix://')) {
    throw new Error('cc+unix:// (unix socket) is not supported by this client yet')
  }
  return url // already http:// or https://
}

/** Resolve the agent-server command (env override, else `clawcodex agent-server`). */
function resolveAgentCmd(): string[] {
  const raw = process.env['CLAWCODEX_AGENT_SERVER_CMD']?.trim()
  return raw ? raw.split(/\s+/) : ['clawcodex', 'agent-server']
}

async function main(): Promise<void> {
  const { url, token, cwd } = parseArgs(process.argv.slice(2))

  let transport: Transport
  let serverLabel: string
  let dispose: (() => void) | undefined

  if (url) {
    // Attach to an already-running / remote server over WebSocket.
    let info
    try {
      info = await createSession(toHttpUrl(url), cwd, token)
    } catch (err) {
      console.error(`clawcodex-tui: failed to create session: ${(err as Error).message}`)
      process.exit(1)
      return
    }
    transport = new WsTransport(info.wsUrl, info.authToken)
    serverLabel = url
  } else {
    // Spawn + own the Python backend over stdio (hermes route). The child emits
    // system/init when ready; the app shows "starting…" until then.
    process.stderr.write(
      'clawcodex-tui: starting agent-server (first launch can take ~20s)…\n',
    )
    const [cmd, ...base] = resolveAgentCmd()
    const stdio = new StdioTransport(cmd!, [...base, '--stdio'], { cwd })
    transport = stdio
    serverLabel = 'local' // spawn mode: the backend is internal
    dispose = () => stdio.close()
    // Make sure the child dies with us, however we exit.
    const cleanup = () => dispose?.()
    process.on('exit', cleanup)
    process.on('SIGINT', () => {
      cleanup()
      process.exit(0)
    })
    process.on('SIGTERM', () => {
      cleanup()
      process.exit(0)
    })
  }

  const { waitUntilExit } = render(<App transport={transport} serverLabel={serverLabel} />)
  await waitUntilExit()
  dispose?.()
}

void main()

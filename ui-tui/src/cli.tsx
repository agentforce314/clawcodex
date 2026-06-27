#!/usr/bin/env node
/**
 * `clawcodex-tui` — the TypeScript Ink TUI.
 *
 * Two modes:
 *   - spawn (default): the TUI is the parent — it spawns the Python
 *     agent-server as a child it owns (the hermes-agent route), reads the
 *     child's `cc://` URL, connects, and tears the child down on exit.
 *   - attach: pass a `cc://`/`http://` URL to connect to an already-running
 *     `clawcodex agent-server` (e.g. a remote one).
 *
 * Usage:
 *   clawcodex-tui [--cwd DIR]                                # spawn + own a backend
 *   clawcodex-tui <cc://host:port> [--token T] [--cwd DIR]   # attach
 */
import { render } from 'ink'
import React from 'react'
import { App } from './App.js'
import { createSession } from './client.js'
import { spawnBackend } from './spawnBackend.js'

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

async function main(): Promise<void> {
  const { url, token, cwd } = parseArgs(process.argv.slice(2))

  let connectUrl: string
  let connectToken: string | undefined
  let serverLabel: string
  let dispose: (() => void) | undefined

  if (url) {
    // Attach to an already-running server.
    connectUrl = url
    connectToken = token
    serverLabel = url
  } else {
    // Spawn + own the Python backend (hermes route).
    process.stderr.write(
      'clawcodex-tui: starting agent-server (first launch can take ~20s)…\n',
    )
    let backend
    try {
      backend = await spawnBackend({ cwd })
    } catch (err) {
      console.error(`clawcodex-tui: ${(err as Error).message}`)
      process.exit(1)
      return
    }
    connectUrl = backend.ccUrl
    connectToken = backend.token
    serverLabel = 'local' // spawn mode: the backend is internal — don't show the ephemeral URL
    dispose = backend.dispose
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

  let info
  try {
    info = await createSession(toHttpUrl(connectUrl), cwd, connectToken)
  } catch (err) {
    console.error(`clawcodex-tui: failed to create session: ${(err as Error).message}`)
    dispose?.()
    process.exit(1)
    return
  }

  const { waitUntilExit } = render(<App info={info} serverLabel={serverLabel} />)
  await waitUntilExit()
  dispose?.()
}

void main()

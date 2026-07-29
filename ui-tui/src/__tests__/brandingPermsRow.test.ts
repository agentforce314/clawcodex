import { PassThrough } from 'stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import { SessionPanel } from '../components/branding.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'
import type { SessionInfo } from '../types.js'

/**
 * The startup header's permission row.
 *
 * It matters more than an ordinary identity row: Full Access is the default, and
 * the live indicator (the composer badge) hides whenever the user is typing, so
 * this row is the one unmissable disclosure of what the session may do. It also
 * names the command that changes it.
 *
 * SessionPanel reads its width from useStdout(), which is hardcoded to
 * process.stdout — pin that, not the PassThrough (see brandingRenderWidth's
 * note; assigning `columns` to the stream leaves the component on its `?? 100`
 * fallback and silently tests one layout at every width).
 */
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

const originalColumns = Object.getOwnPropertyDescriptor(process.stdout, 'columns')

afterEach(() => {
  if (originalColumns) {
    Object.defineProperty(process.stdout, 'columns', originalColumns)

    return
  }

  delete (process.stdout as { columns?: number }).columns
})

const info = (permission_mode?: string): SessionInfo => ({
  cwd: '/Users/dev/workspace/proj',
  model: 'anthropic/claude-opus-5',
  permission_mode,
  skills: {},
  tools: {},
  version: '1.3.0'
})

async function render(permission_mode: string | undefined, cols = 100): Promise<string> {
  Object.defineProperty(process.stdout, 'columns', { configurable: true, value: cols, writable: true })

  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()

  Object.assign(stdout, { columns: cols, isTTY: false, rows: 60 })
  Object.assign(stdin, { isTTY: false })

  let captured = ''

  stdout.on('data', chunk => {
    captured += chunk.toString()
  })

  const instance = renderSync(
    React.createElement(SessionPanel, { info: info(permission_mode), sid: 'a1b2c3d4', t: DEFAULT_THEME }),
    {
      patchConsole: false,
      stderr: stderr as NodeJS.WriteStream,
      stdin: stdin as NodeJS.ReadStream,
      stdout: stdout as NodeJS.WriteStream
    }
  )

  try {
    await delay(30)

    return stripAnsi(captured)
  } finally {
    instance.unmount()
  }
}

describe('SessionPanel — permission row', () => {
  it('names the level and the command that changes it', async () => {
    const out = await render('bypassPermissions')

    expect(out).toContain('Full Access')
    expect(out).toContain('/permissions')
  })

  it('is not truncated at the default width', async () => {
    // The row is in optimalLeftWidth's sizing set precisely because it isn't
    // covered by the other rows — sized off them it rendered "…/permission…",
    // which is the row that must stay legible now that Full Access is default.
    const out = await render('bypassPermissions')

    expect(out).toContain('Full Access · /permissions')
    expect(out).not.toContain('/permission…')
  })

  it('renders the other levels by their picker labels', async () => {
    expect(await render('default')).toContain('Ask for approval')
    expect(await render('acceptEdits')).toContain('Approve for me')
  })

  it('shows an off-ladder mode under its raw name, not a level label', async () => {
    // plan / dontAsk are real modes the picker deliberately does not list.
    // They still render — labeling them as one of the three would be a lie,
    // but omitting them hides them entirely.
    const out = await render('plan')

    expect(out).toContain('plan')
    expect(out).not.toContain('Full Access')
    expect(out).not.toContain('Ask for approval')
  })

  it('surfaces a repo-forced dontAsk, which denies everything that would prompt', async () => {
    // A repository settings file may set this. The composer badge is the only
    // other indicator and it hides while the user types, so without this row a
    // user whose agent refuses every action has nothing on screen to trace it to.
    expect(await render('dontAsk')).toContain('dontAsk')
  })

  it('omits the row when the backend reported no mode', async () => {
    expect(await render(undefined)).not.toContain('Perms')
  })
})

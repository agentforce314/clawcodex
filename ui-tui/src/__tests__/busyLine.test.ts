import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  process.env.FORCE_COLOR = '3'
  process.env.COLORTERM = 'truecolor'
  delete process.env.NO_COLOR
})

import { patchTurnState, resetTurnState } from '../app/turnStore.js'
import { patchUiState } from '../app/uiStore.js'
import { BusyLine } from '../components/busyLine.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

const renderToString = (element: React.ReactElement): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 100, isTTY: false, rows: 30 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  const instance = renderSync(element, {
    patchConsole: false,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  instance.unmount()
  instance.cleanup()

  return output
}

const busyRender = (turnStartedAt: number) => {
  patchUiState({ busy: true })

  return renderToString(React.createElement(BusyLine, { t: DEFAULT_THEME, turnStartedAt }))
}

describe('BusyLine', () => {
  it('renders nothing when idle (no persistent chrome)', () => {
    resetTurnState()
    patchUiState({ busy: false })

    const plain = stripAnsi(renderToString(React.createElement(BusyLine, { t: DEFAULT_THEME, turnStartedAt: null })))

    expect(plain.trim()).toBe('')
  })

  it('shows the in-progress todo activeForm as the verb and the pending todo as Next', () => {
    resetTurnState()
    patchTurnState({
      lastDeltaAt: Date.now(),
      todos: [
        { activeForm: 'Extracting the loader', content: 'Extract loader', id: '1', status: 'in_progress' },
        { content: 'Add unit tests', id: '2', status: 'pending' }
      ]
    })

    const plain = stripAnsi(busyRender(Date.now()))

    expect(plain).toContain('Extracting the loader…')
    expect(plain).toContain('Next: Add unit tests')
    // under 30s: no elapsed/token suffix
    expect(plain).not.toContain('tokens')
  })

  it('adds the dim elapsed + ~token suffix only after 30s', () => {
    resetTurnState()
    patchTurnState({ lastDeltaAt: Date.now(), streamedChars: 4800 })

    const plain = stripAnsi(busyRender(Date.now() - 45_000))

    expect(plain).toMatch(/\(45s · ↓ ~1\.2k tokens\)/)
  })

  it('cuts glyph+verb to the stall red after 3s without deltas', () => {
    resetTurnState()
    patchTurnState({ lastDeltaAt: Date.now() - 10_000, tools: [] })

    const output = busyRender(Date.now())

    expect(output).toContain('171;43;63')
  })

  it('does not false-stall between tools (tool lifecycle counts as liveness)', () => {
    resetTurnState()
    // Simulate the inter-tool gap: last stamp recent because a tool just
    // completed, no text deltas at all this turn.
    patchTurnState({ lastDeltaAt: Date.now() - 1_000, streamedChars: 0, tools: [] })

    const output = busyRender(Date.now() - 10_000)

    expect(output).not.toContain('171;43;63')
  })

  it('shows the delegation segment only while fanning out', () => {
    resetTurnState()
    patchTurnState({
      lastDeltaAt: Date.now(),
      subagents: [
        { depth: 1, goal: 'explore', id: 'sa1', status: 'running' } as never
      ]
    })

    const plain = stripAnsi(busyRender(Date.now()))

    expect(plain).toContain('⛓')
  })
})

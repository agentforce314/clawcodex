import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  process.env.FORCE_COLOR = '3'
  process.env.COLORTERM = 'truecolor'
  delete process.env.NO_COLOR
})

import { ComposerFooter } from '../components/composerFooter.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

const renderToString = (element: React.ReactElement): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 100, isTTY: false, rows: 20 })
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

const footer = (props: Partial<Parameters<typeof ComposerFooter>[0]> = {}) =>
  renderToString(
    React.createElement(ComposerFooter, {
      busy: false,
      inputEmpty: true,
      mode: 'default',
      sh: false,
      t: DEFAULT_THEME,
      ...props
    })
  )

describe('ComposerFooter', () => {
  it('shows the idle shortcuts hint in default mode', () => {
    expect(stripAnsi(footer())).toContain('? for shortcuts')
  })

  it('suppresses everything while the input has text (suppressHint parity)', () => {
    expect(stripAnsi(footer({ inputEmpty: false })).trim()).toBe('')
  })

  it('swaps the hint for the interrupt hint while busy', () => {
    const plain = stripAnsi(footer({ busy: true }))

    expect(plain).toContain('ctrl+c to interrupt')
    expect(plain).not.toContain('? for shortcuts')
  })

  it('renders each permission-mode badge with its color and the cycle hint', () => {
    const plan = footer({ mode: 'plan' })

    expect(stripAnsi(plan)).toContain('⏸ plan mode on (shift+tab to cycle)')
    expect(plan).toContain('72;150;140') // planMode sage
    // hint coexists with the badge
    expect(stripAnsi(plan)).toContain('? for shortcuts')

    const accept = footer({ mode: 'acceptEdits' })

    expect(stripAnsi(accept)).toContain('▶▶ accept edits on')
    expect(accept).toContain('175;135;255') // autoAccept violet

    const bypass = footer({ mode: 'bypassPermissions' })

    expect(stripAnsi(bypass)).toContain('▶▶ bypass permissions on')
    expect(bypass).toContain('255;107;128') // error red
  })

  it('shows bash-mode hint in pink when the input is in ! mode', () => {
    const out = footer({ sh: true })

    expect(stripAnsi(out)).toContain('! for bash mode')
    expect(out).toContain('253;93;177')
  })

  it('shows the voice label only while voice is actually active', () => {
    expect(stripAnsi(footer({ voiceLabel: 'voice off' }))).not.toContain('voice off')
    expect(stripAnsi(footer({ voiceLabel: '● rec 0:04' }))).toContain('● rec 0:04')
  })
})

// ── permission-mode store refresh (message.complete piggyback) ──────────────

import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { $uiState, patchUiState } from '../app/uiStore.js'

const ref = <T,>(current: T) => ({ current })

const buildHandlerCtx = () =>
  ({
    composer: {
      dequeue: () => undefined,
      queueEditRef: ref<null | number>(null),
      sendQueued: vi.fn(),
      setInput: vi.fn()
    },
    gateway: { gw: { request: vi.fn() }, rpc: vi.fn(async () => null) },
    session: {
      STARTUP_RESUME_ID: '',
      colsRef: ref(80),
      newSession: vi.fn(),
      resetSession: vi.fn(),
      resumeById: vi.fn(),
      setCatalog: vi.fn()
    },
    submission: { submitRef: { current: vi.fn() } },
    system: { bellOnComplete: false, sys: vi.fn() },
    transcript: { appendMessage: vi.fn(), panel: vi.fn(), setHistoryItems: vi.fn() },
    voice: { setProcessing: vi.fn(), setRecording: vi.fn(), setVoiceEnabled: vi.fn() }
  }) as never

describe('permissionMode store refresh', () => {
  it('patches uiState from the end-of-turn result payload', () => {
    patchUiState({ permissionMode: 'plan' })

    const handler = createGatewayEventHandler(buildHandlerCtx())

    handler({ payload: { permission_mode: 'acceptEdits', text: 'done' }, type: 'message.complete' } as never)

    expect($uiState.get().permissionMode).toBe('acceptEdits')
  })

  it('patches uiState from the permission.mode event (/mode while idle)', () => {
    patchUiState({ permissionMode: 'default' })

    const handler = createGatewayEventHandler(buildHandlerCtx())

    handler({ payload: { mode: 'plan' }, type: 'permission.mode' } as never)

    expect($uiState.get().permissionMode).toBe('plan')
  })
})

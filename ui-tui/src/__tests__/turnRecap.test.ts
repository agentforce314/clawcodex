import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { resetOverlayState } from '../app/overlayStore.js'
import { turnController } from '../app/turnController.js'
import { resetTurnState } from '../app/turnStore.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'
import { shouldAcceptPendingSuggestion } from '../app/useInputHandlers.js'
import { composerPlaceholder, PLACEHOLDER } from '../content/placeholders.js'
import type { Msg } from '../types.js'

// Fake child process for the GatewayClient NDJSON translation test — same
// harness as gatewayClient.test.ts (feed protocol lines, observe events).
const harness = vi.hoisted(() => ({ proc: null as null | EventEmitter }))

vi.mock('node:child_process', () => ({
  spawn: () => harness.proc
}))

import { GatewayClient } from '../gatewayClient.js'

class FakeProc extends EventEmitter {
  kill = vi.fn()
  stderr = new PassThrough()
  stdin = new PassThrough()
  stdout = new PassThrough()

  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

const ref = <T>(current: T) => ({ current })

const buildCtx = (appended: Msg[]) =>
  ({
    composer: {
      dequeue: () => undefined,
      queueEditRef: ref<null | number>(null),
      sendQueued: vi.fn(),
      setInput: vi.fn()
    },
    gateway: {
      gw: { request: vi.fn() },
      rpc: vi.fn(async () => null)
    },
    session: {
      STARTUP_RESUME_ID: '',
      colsRef: ref(80),
      newSession: vi.fn(),
      resetSession: vi.fn(),
      resumeById: vi.fn(),
      setCatalog: vi.fn()
    },
    submission: {
      submitRef: { current: vi.fn() }
    },
    system: {
      bellOnComplete: false,
      sys: vi.fn()
    },
    transcript: {
      appendMessage: (msg: Msg) => appended.push(msg),
      panel: vi.fn(),
      setHistoryItems: vi.fn()
    },
    voice: {
      setProcessing: vi.fn(),
      setRecording: vi.fn(),
      setVoiceEnabled: vi.fn()
    }
  }) as any

describe('turn.recap event handling', () => {
  beforeEach(() => {
    resetOverlayState()
    resetUiState()
    resetTurnState()
    turnController.fullReset()
  })

  it('appends the recap transcript line and arms the composer suggestion when idle', () => {
    const appended: Msg[] = []
    const onEvent = createGatewayEventHandler(buildCtx(appended))

    onEvent({
      payload: { recap: 'Goal was X. Done. Next: re-run.', suggestion: 'fix all four issues and re-run' },
      type: 'turn.recap'
    } as any)

    expect(appended).toEqual([
      { kind: 'recap', role: 'system', text: 'Goal was X. Done. Next: re-run.' }
    ])
    expect(getUiState().pendingSuggestion).toBe('fix all four issues and re-run')
  })

  it('drops the whole event when a new turn is already running (stale recap)', () => {
    const appended: Msg[] = []
    const onEvent = createGatewayEventHandler(buildCtx(appended))

    patchUiState({ busy: true })
    onEvent({
      payload: { recap: 'Goal was X. Next: Y.', suggestion: 'do Y' },
      type: 'turn.recap'
    } as any)

    expect(appended).toEqual([])
    expect(getUiState().pendingSuggestion).toBeNull()
  })

  it('renders a recap without a suggestion (and vice versa) independently', () => {
    const appended: Msg[] = []
    const onEvent = createGatewayEventHandler(buildCtx(appended))

    onEvent({ payload: { recap: 'Only a recap.', suggestion: '' }, type: 'turn.recap' } as any)
    expect(appended).toHaveLength(1)
    expect(getUiState().pendingSuggestion).toBeNull()

    onEvent({ payload: { recap: '', suggestion: 'continue the port' }, type: 'turn.recap' } as any)
    expect(appended).toHaveLength(1) // no second transcript line
    expect(getUiState().pendingSuggestion).toBe('continue the port')
  })

  it('startMessage expires the pending suggestion at the next turn start', () => {
    patchUiState({ pendingSuggestion: 'fix all four issues and re-run' })
    turnController.startMessage()
    expect(getUiState().pendingSuggestion).toBeNull()
    expect(getUiState().busy).toBe(true)
  })
})

describe('composerPlaceholder — what the ghost slot shows', () => {
  it('shows the armed suggestion MID-conversation (the shipped regression: conversationEmpty gated it off exactly where suggestions exist)', () => {
    expect(
      composerPlaceholder({ busy: false, conversationEmpty: false, pendingSuggestion: 'fix all four issues and re-run' })
    ).toBe('fix all four issues and re-run')
  })

  it('suggestion wins over the fresh-conversation hint too', () => {
    expect(composerPlaceholder({ busy: false, conversationEmpty: true, pendingSuggestion: 'do it' })).toBe('do it')
  })

  it('keeps the static hint fresh-conversation-only', () => {
    expect(composerPlaceholder({ busy: false, conversationEmpty: true, pendingSuggestion: null })).toBe(PLACEHOLDER)
    expect(composerPlaceholder({ busy: false, conversationEmpty: false, pendingSuggestion: null })).toBe('')
  })

  it('busy blanks the slot regardless', () => {
    expect(composerPlaceholder({ busy: true, conversationEmpty: false, pendingSuggestion: 'x' })).toBe('')
    expect(composerPlaceholder({ busy: true, conversationEmpty: true, pendingSuggestion: null })).toBe('')
  })
})

describe('shouldAcceptPendingSuggestion — Tab accepts only what is visibly suggested', () => {
  const visible = { busy: false, completionsLen: 0, input: '', suggestion: 'fix the tests' }

  it('accepts plain Tab while the ghost suggestion is showing (mid-conversation)', () => {
    expect(shouldAcceptPendingSuggestion({ shift: false, tab: true }, visible)).toBe(true)
  })

  it('never fires for Shift+Tab — that cycles the permission mode', () => {
    expect(shouldAcceptPendingSuggestion({ shift: true, tab: true }, visible)).toBe(false)
  })

  it('never fires for non-Tab keys', () => {
    expect(shouldAcceptPendingSuggestion({ shift: false, tab: false }, visible)).toBe(false)
  })

  it('defers to an open completion menu', () => {
    expect(
      shouldAcceptPendingSuggestion({ shift: false, tab: true }, { ...visible, completionsLen: 2 })
    ).toBe(false)
  })

  it('stays inert once the user typed something (ghost is hidden)', () => {
    expect(shouldAcceptPendingSuggestion({ shift: false, tab: true }, { ...visible, input: 'h' })).toBe(false)
  })

  it('stays inert while a turn is running', () => {
    expect(shouldAcceptPendingSuggestion({ shift: false, tab: true }, { ...visible, busy: true })).toBe(false)
  })

  it('stays inert with no suggestion armed', () => {
    expect(
      shouldAcceptPendingSuggestion({ shift: false, tab: true }, { ...visible, suggestion: null })
    ).toBe(false)
  })
})

describe('GatewayClient system/recap translation', () => {
  const prevWs = process.env.CLAWCODEX_WORKSPACE
  let events: any[]
  let gw: GatewayClient
  let proc: FakeProc

  beforeEach(() => {
    process.env.CLAWCODEX_WORKSPACE = '/ws'
    proc = new FakeProc()
    harness.proc = proc
    events = []
    gw = new GatewayClient()
    gw.on('event', (e: any) => events.push(e))
    gw.start()
    gw.drain()
  })

  afterEach(() => {
    gw.kill()

    if (prevWs === undefined) {delete process.env.CLAWCODEX_WORKSPACE}
    else {process.env.CLAWCODEX_WORKSPACE = prevWs}
  })

  it('maps the NDJSON system/recap frame to a turn.recap event', async () => {
    proc.line({
      recap: 'Goal was porting the recap feature. Next: run the tests.',
      session_id: 's1',
      subtype: 'recap',
      suggestion: 'run the tests',
      type: 'system'
    })

    await vi.waitFor(() => expect(events.find(e => e.type === 'turn.recap')).toBeTruthy())
    expect(events.find(e => e.type === 'turn.recap').payload).toEqual({
      recap: 'Goal was porting the recap feature. Next: run the tests.',
      suggestion: 'run the tests'
    })
  })

  it('coerces missing fields to empty strings', async () => {
    proc.line({ session_id: 's1', subtype: 'recap', type: 'system' })

    await vi.waitFor(() => expect(events.find(e => e.type === 'turn.recap')).toBeTruthy())
    expect(events.find(e => e.type === 'turn.recap').payload).toEqual({ recap: '', suggestion: '' })
  })

  it('lists /recap in the commands catalog with no hint (local argumentHint is authoritative)', async () => {
    // The completion menu is built from the gateway catalog, NOT the local
    // slash registry — a runnable-but-undiscoverable /recap already
    // regressed once this cycle, so pin the catalog entry itself.
    proc.line({ cwd: '/ws', session_id: 's1', subtype: 'init', type: 'system' })
    const p = gw.request<{ hints: Record<string, string>; pairs: [string, string][] }>('commands.catalog', {})

    // The catalog awaits the backend's workflow-command list; feed the
    // control reply like gatewayClient.test.ts's replyToControl does.
    let req: any
    await vi.waitFor(() => {
      const raw = (proc.stdin as any).read()?.toString() ?? ''
      const frames = raw.split('\n').filter(Boolean).map((l: string) => JSON.parse(l))

      req = req ?? frames.find((f: any) => f.type === 'control_request' && f.request?.subtype === 'list_workflow_commands')
      expect(req).toBeTruthy()
    })
    proc.line({ response: { request_id: req.request_id, response: { commands: [] } }, type: 'control_response' })

    const catalog = await p

    expect(catalog.pairs.some(([name]) => name === '/recap')).toBe(true)
    expect(catalog.hints['/recap']).toBeUndefined()
  })
})

describe('suggestion expiry wiring (source pin)', () => {
  // resetSession/resetVisibleHistory are closures inside useSessionLifecycle
  // — no hook harness exists in this suite, so pin the wiring at source
  // level: both reset paths must null pendingSuggestion (the /clear, /new,
  // /resume, and history-reset routes). The behavioral halves are covered
  // by the startMessage test above and the uiStore default.
  it('both session-reset paths expire pendingSuggestion', async () => {
    const fs = await import('node:fs')
    const src = fs.readFileSync(new URL('../app/useSessionLifecycle.ts', import.meta.url), 'utf8')

    expect(src.split('pendingSuggestion: null').length - 1).toBe(2)
  })
})

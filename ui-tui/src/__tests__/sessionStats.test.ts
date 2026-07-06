/**
 * Session-stats line under the composer — the deleted REPL's bottom toolbar:
 * `provider · model · cwd · turns: N · tokens: X in / Y out · cost $C`.
 *
 * Covers the CostSnapshot fold, the width-aware line builder, the
 * message.complete → ui.sessionStats patch, and the component render gate.
 */
import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { resetOverlayState } from '../app/overlayStore.js'
import { turnController } from '../app/turnController.js'
import { resetTurnState } from '../app/turnStore.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'
import { SessionStatsLine } from '../components/sessionStatsLine.js'
import type { CostSnapshot } from '../gatewayTypes.js'
import { buildSessionStatsLine, statsFromCostSnapshot, ZERO_SESSION_STATS } from '../lib/sessionStats.js'
import { stripAnsi } from '../lib/text.js'
import type { Msg } from '../types.js'

const SNAPSHOT: CostSnapshot = {
  model_usage: {
    'deepseek-chat': {
      cache_creation_input_tokens: 100,
      cache_read_input_tokens: 2000,
      cost_usd: 0.004,
      input_tokens: 31089,
      output_tokens: 600
    },
    'deepseek-reasoner': { cost_usd: 0.0008, input_tokens: 0, output_tokens: 22 }
  },
  total_cost_usd: 0.0048
}

describe('statsFromCostSnapshot', () => {
  it('folds per-model accumulators, counting cache reads/writes as input', () => {
    expect(statsFromCostSnapshot(SNAPSHOT, 3)).toEqual({
      costUsd: 0.0048,
      inputTokens: 33189,
      outputTokens: 622,
      turns: 3
    })
  })

  it('degrades an empty snapshot to zeros', () => {
    expect(statsFromCostSnapshot({}, 1)).toEqual({ ...ZERO_SESSION_STATS, turns: 1 })
  })
})

describe('buildSessionStatsLine', () => {
  const HOME = process.env.HOME

  beforeEach(() => {
    process.env.HOME = '/Users/test'
  })

  afterEach(() => {
    if (HOME === undefined) {delete process.env.HOME}
    else {process.env.HOME = HOME}
  })

  const stats = { costUsd: 0.0048, inputTokens: 33189, outputTokens: 622, turns: 1 }

  const build = (cols: number, overrides: Partial<Parameters<typeof buildSessionStatsLine>[0]> = {}) =>
    buildSessionStatsLine({
      cols,
      cwd: '/Users/test/workspace/clawcodex',
      model: 'deepseek-v4-flash',
      provider: 'deepseek',
      stats,
      ...overrides
    })

  it('renders the full toolbar line when it fits (REPL toolbar parity)', () => {
    expect(build(200)).toBe(
      'deepseek · deepseek-v4-flash · /Users/test/workspace/clawcodex · ' +
        'turns: 1 · tokens: 33189 in / 622 out · cost $0.0048'
    )
  })

  it('hides the cost segment while nothing has been spent', () => {
    expect(build(200, { stats: { ...stats, costUsd: 0 } })).toBe(
      'deepseek · deepseek-v4-flash · /Users/test/workspace/clawcodex · turns: 1 · tokens: 33189 in / 622 out'
    )
  })

  it('sheds cwd detail as the terminal narrows: ~ first, then a tail, then nothing', () => {
    expect(build(110)).toContain(' · ~/workspace/clawcodex · ')
    expect(build(100)).toContain(' · …e/clawcodex · ')
    expect(build(90)).toBe('deepseek · deepseek-v4-flash · turns: 1 · tokens: 33189 in / 622 out · cost $0.0048')
  })

  it('tilde-abbreviates only at a path boundary (/Users/testuser is not under /Users/test)', () => {
    const under = `/Users/test/${'a'.repeat(30)}`
    const sibling = `/Users/testuser/${'a'.repeat(26)}`

    // Same length (42): the under-home cwd shrinks to ~/… and fits; the
    // sibling must NOT become ~user/… — it falls through to the … tail.
    expect(build(120, { cwd: under })).toContain(` · ~/${'a'.repeat(30)} · `)

    const shed = build(120, { cwd: sibling })
    expect(shed).not.toContain('~user')
    expect(shed).toContain(' · …')
  })

  it('omits empty segments instead of doubling separators', () => {
    expect(build(200, { cwd: '', provider: '' })).toBe(
      'deepseek-v4-flash · turns: 1 · tokens: 33189 in / 622 out · cost $0.0048'
    )
  })
})

describe('message.complete → ui.sessionStats', () => {
  const buildCtx = () =>
    ({
      composer: {
        dequeue: () => undefined,
        queueEditRef: { current: null },
        sendQueued: vi.fn(),
        setInput: vi.fn()
      },
      gateway: { gw: { request: vi.fn() }, rpc: vi.fn(async () => null) },
      session: {
        STARTUP_RESUME_ID: '',
        colsRef: { current: 80 },
        newSession: vi.fn(),
        resetSession: vi.fn(),
        resumeById: vi.fn(),
        setCatalog: vi.fn()
      },
      submission: { submitRef: { current: vi.fn() } },
      system: { bellOnComplete: false, sys: vi.fn() },
      transcript: {
        appendMessage: (_msg: Msg) => undefined,
        panel: vi.fn(),
        setHistoryItems: vi.fn()
      },
      voice: { setProcessing: vi.fn(), setRecording: vi.fn(), setVoiceEnabled: vi.fn() }
    }) as any

  beforeEach(() => {
    resetOverlayState()
    resetUiState()
    resetTurnState()
    turnController.fullReset()
  })

  it('folds the end-of-turn snapshot + odometer into sessionStats', () => {
    const onEvent = createGatewayEventHandler(buildCtx())

    onEvent({ payload: { cost: SNAPSHOT, session_turns: 1, text: 'done' }, type: 'message.complete' } as any)

    expect(getUiState().sessionStats).toEqual({
      costUsd: 0.0048,
      inputTokens: 33189,
      outputTokens: 622,
      turns: 1
    })
  })

  it('keeps totals when the best-effort snapshot comes back empty', () => {
    const onEvent = createGatewayEventHandler(buildCtx())

    onEvent({ payload: { cost: SNAPSHOT, session_turns: 1, text: 'a' }, type: 'message.complete' } as any)
    onEvent({ payload: { cost: {}, session_turns: 2, text: 'b' }, type: 'message.complete' } as any)

    expect(getUiState().sessionStats).toEqual({
      costUsd: 0.0048,
      inputTokens: 33189,
      outputTokens: 622,
      turns: 2
    })
  })

  it('leaves sessionStats untouched when the payload carries neither field', () => {
    const onEvent = createGatewayEventHandler(buildCtx())
    const before = getUiState().sessionStats

    onEvent({ payload: { text: 'plain' }, type: 'message.complete' } as any)

    expect(getUiState().sessionStats).toBe(before)
  })

  it('folds an out-of-band session.stats event (/clear and /resume reply riders)', () => {
    const onEvent = createGatewayEventHandler(buildCtx())

    onEvent({ payload: { cost: SNAPSHOT, session_turns: 5, text: 'a' }, type: 'message.complete' } as any)
    // /clear rider: odometer resets, spend totals persist.
    onEvent({ payload: { cost: SNAPSHOT, session_turns: 0 }, type: 'session.stats' } as any)

    expect(getUiState().sessionStats).toEqual({
      costUsd: 0.0048,
      inputTokens: 33189,
      outputTokens: 622,
      turns: 0
    })
  })
})

describe('SessionStatsLine', () => {
  const renderToString = (element: React.ReactElement): string => {
    const stdout = new PassThrough()
    const stdin = new PassThrough()
    const stderr = new PassThrough()
    let output = ''

    Object.assign(stdout, { columns: 120, isTTY: false, rows: 20 })
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

  beforeEach(() => {
    resetUiState()
  })

  it('stays hidden until session.info arrives', () => {
    const out = renderToString(React.createElement(SessionStatsLine, { cols: 120 }))

    expect(stripAnsi(out).trim()).toBe('')
  })

  it('renders provider · model · cwd and the accumulators once ready', () => {
    patchUiState({
      info: { cwd: '/w/app', model: 'deepseek-v4-flash', profile_name: 'deepseek', skills: {}, tools: {} } as any,
      sessionStats: { costUsd: 0.0048, inputTokens: 33189, outputTokens: 622, turns: 1 }
    })

    const out = stripAnsi(renderToString(React.createElement(SessionStatsLine, { cols: 120 })))

    expect(out).toContain('deepseek · deepseek-v4-flash · /w/app · turns: 1 · tokens: 33189 in / 622 out · cost $0.0048')
  })
})

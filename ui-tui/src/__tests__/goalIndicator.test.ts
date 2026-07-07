import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  process.env.FORCE_COLOR = '3'
  process.env.COLORTERM = 'truecolor'
  delete process.env.NO_COLOR
})

import { $goalState, applyGoalSnapshot, resetGoalState } from '../app/goalStore.js'
import { GoalIndicator } from '../components/goalIndicator.js'
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

const render = () => renderToString(React.createElement(GoalIndicator, { t: DEFAULT_THEME }))

describe('goalStore.applyGoalSnapshot', () => {
  afterEach(() => resetGoalState())

  it('maps a wire snapshot (epoch seconds) to indicator state (epoch ms)', () => {
    applyGoalSnapshot({ created_at: 1_751_800_000, goal: 'ship it', max_turns: 20, status: 'active', turns_used: 3 })

    expect($goalState.get()).toEqual({
      maxTurns: 20,
      startedAt: 1_751_800_000_000,
      status: 'active',
      turnsUsed: 3
    })
  })

  it('hides on null / unknown status / non-object garbage', () => {
    applyGoalSnapshot({ created_at: 1, goal: 'x', status: 'active' })
    applyGoalSnapshot(null)
    expect($goalState.get()).toBeNull()

    applyGoalSnapshot({ goal: 'x', status: 'done' })
    expect($goalState.get()).toBeNull()

    applyGoalSnapshot('garbage' as never)
    expect($goalState.get()).toBeNull()
  })

  it('falls back to Date.now when created_at is missing or junk', () => {
    const before = Date.now()
    applyGoalSnapshot({ goal: 'x', status: 'active' })

    const state = $goalState.get()

    expect(state?.startedAt).toBeGreaterThanOrEqual(before)
    expect(state?.startedAt).toBeLessThanOrEqual(Date.now())
  })

  it('drops carriers whose rev is at or below the last applied one', () => {
    // Fresh state at rev 5 (say, /goal pause reply)…
    applyGoalSnapshot({ created_at: 1, goal: 'x', status: 'paused' }, 5)
    // …then a STALE active carrier captured earlier but delivered later
    // (worker post-turn event racing the control reply) must be ignored.
    applyGoalSnapshot({ created_at: 1, goal: 'x', status: 'active' }, 4)
    expect($goalState.get()?.status).toBe('paused')

    applyGoalSnapshot({ created_at: 1, goal: 'x', status: 'active' }, 5)
    expect($goalState.get()?.status).toBe('paused')

    // A stale null (old "done" event) must not hide a newer active state.
    applyGoalSnapshot(null, 3)
    expect($goalState.get()?.status).toBe('paused')

    // Newer rev applies.
    applyGoalSnapshot({ created_at: 1, goal: 'x', status: 'active' }, 6)
    expect($goalState.get()?.status).toBe('active')
  })

  it('rev-less carriers (legacy backend) apply unconditionally', () => {
    applyGoalSnapshot({ created_at: 1, goal: 'x', status: 'active' }, 7)
    applyGoalSnapshot(null)
    expect($goalState.get()).toBeNull()
  })

  it('resetGoalState clears the rev watermark (fresh backend restarts at 1)', () => {
    applyGoalSnapshot({ created_at: 1, goal: 'x', status: 'active' }, 50)
    resetGoalState()
    applyGoalSnapshot({ created_at: 1, goal: 'x', status: 'active' }, 1)
    expect($goalState.get()?.status).toBe('active')
  })
})

describe('GoalIndicator', () => {
  afterEach(() => {
    resetGoalState()
    vi.useRealTimers()
  })

  it('renders nothing without a goal', () => {
    resetGoalState()
    expect(stripAnsi(render()).trim()).toBe('')
  })

  it('shows the ticking elapsed badge for an active goal', () => {
    applyGoalSnapshot({ created_at: (Date.now() - 14_000) / 1000, goal: 'ship it', max_turns: 20, status: 'active', turns_used: 0 })

    const plain = stripAnsi(render())

    expect(plain).toContain('◎ /goal active (14s)')
    // pre-judge (turn 0): no odometer
    expect(plain).not.toContain('turn')
  })

  it('adds the turn odometer once the judge has evaluated turns', () => {
    applyGoalSnapshot({ created_at: (Date.now() - 62_000) / 1000, goal: 'ship it', max_turns: 20, status: 'active', turns_used: 3 })

    expect(stripAnsi(render())).toContain('◎ /goal active (1m 2s · turn 3/20)')
  })

  it('renders in the permission lavender (the reference chrome color)', () => {
    applyGoalSnapshot({ created_at: Date.now() / 1000, goal: 'x', status: 'active' })

    // DEFAULT_THEME permission = rgb(177,185,249)
    expect(render()).toContain('177;185;249')
  })

  it('clamps a future created_at (clock skew) to 0s instead of going negative', () => {
    applyGoalSnapshot({ created_at: (Date.now() + 60_000) / 1000, goal: 'x', status: 'active' })

    expect(stripAnsi(render())).toContain('◎ /goal active (0s)')
  })

  it('shows the muted paused badge with the resume hint', () => {
    applyGoalSnapshot({ created_at: Date.now() / 1000, goal: 'x', status: 'paused' })

    expect(stripAnsi(render())).toContain('⏸ /goal paused · /goal resume')
  })

  it('ticks the elapsed display once per second while mounted', async () => {
    vi.useFakeTimers()
    applyGoalSnapshot({ created_at: (Date.now() - 14_000) / 1000, goal: 'x', status: 'active' })

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

    const instance = renderSync(React.createElement(GoalIndicator, { t: DEFAULT_THEME }), {
      patchConsole: false,
      stderr: stderr as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      stdout: stdout as unknown as NodeJS.WriteStream
    })

    try {
      expect(stripAnsi(output)).toContain('(14s)')

      await vi.advanceTimersByTimeAsync(2_100)

      expect(stripAnsi(output)).toContain('(16s)')
    } finally {
      instance.unmount()
      instance.cleanup()
    }
  })
})

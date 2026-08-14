import { describe, expect, it } from 'vitest'

import type { GatewayEvent } from '../gateway/protocol.ts'
import {
  applyTrajectoryEvent,
  emptyTrajectory,
  formatMs,
  recordPrompt,
  stepMetrics,
  stepTtft,
  toolSummary,
  trajectoryStats,
  type TrajectoryRecord,
  type TrajectoryState,
} from './trajectory.ts'

/** A clock the test drives, so durations are asserted rather than tolerated. */
function clockFrom(start: number) {
  let value = start

  return {
    at: () => value,
    advance(ms: number) {
      value += ms
    },
  }
}

function event(type: string, payload?: unknown): GatewayEvent {
  return { payload, type } as GatewayEvent
}

const kinds = (state: TrajectoryState): string[] => state.records.map(record => record.kind)
const assistants = (state: TrajectoryState): TrajectoryRecord[] =>
  state.records.filter(record => record.kind === 'assistant')
const tools = (state: TrajectoryState): TrajectoryRecord[] =>
  state.records.filter(record => record.kind === 'tool')

describe('a single-step turn', () => {
  it('records the prompt, the step, and its timings', () => {
    const clock = clockFrom(1_000)
    let state = recordPrompt(emptyTrajectory(), 'hello', clock.at)

    clock.advance(300) // thinking before the first token
    state = applyTrajectoryEvent(state, event('message.delta', { text: 'Hi ' }), clock.at)
    clock.advance(700) // generating
    state = applyTrajectoryEvent(state, event('message.delta', { text: 'there' }), clock.at)
    state = applyTrajectoryEvent(
      state,
      event('step.complete', {
        model: 'deepseek-v4-pro',
        stop_reason: 'stop',
        usage: { calls: 1, input: 100, output: 20, total: 120 },
      }),
      clock.at,
    )
    state = applyTrajectoryEvent(state, event('message.complete', { status: 'ok' }), clock.at)

    expect(kinds(state)).toEqual(['user', 'assistant'])

    const step = assistants(state)[0]
    expect(step?.turn).toBe(1)
    expect(step?.step).toBe(1)
    expect(step?.detail).toBe('Hi there')
    expect(step?.metrics?.model).toBe('deepseek-v4-pro')

    // TTFT is prompt→first token; total is prompt→completion.
    expect(stepTtft(step?.metrics)).toBe('300 ms')
    expect(stepMetrics(step?.metrics).total).toBe('1.00 s')
    // Generation is first token→completion: 700ms for 20 tokens.
    expect(stepMetrics(step?.metrics).generation).toBe('700 ms')
    expect(stepMetrics(step?.metrics).throughput).toBe('28.6 tok/s')
  })

  it('counts reasoning as the first token', () => {
    const clock = clockFrom(0)
    let state = recordPrompt(emptyTrajectory(), 'q', clock.at)

    clock.advance(120)
    state = applyTrajectoryEvent(state, event('reasoning.delta', { text: 'hmm' }), clock.at)
    clock.advance(400)
    state = applyTrajectoryEvent(state, event('message.delta', { text: 'answer' }), clock.at)

    expect(stepTtft(assistants(state)[0]?.metrics)).toBe('120 ms')
    expect(assistants(state)[0]?.thinking).toBe('hmm')
  })
})

describe('a multi-step turn', () => {
  it('numbers steps, and starts each one when the previous tools finished', () => {
    const clock = clockFrom(0)
    let state = recordPrompt(emptyTrajectory(), 'do it', clock.at)

    // Step 1: think, then call a tool.
    clock.advance(100)
    state = applyTrajectoryEvent(state, event('message.delta', { text: 'Let me look.' }), clock.at)
    clock.advance(50)
    state = applyTrajectoryEvent(
      state,
      event('step.complete', { usage: { calls: 1, input: 10, output: 5, total: 15 } }),
      clock.at,
    )
    state = applyTrajectoryEvent(
      state,
      event('tool.start', { args: { command: 'ls' }, name: 'terminal', tool_id: 't1' }),
      clock.at,
    )

    // The tool runs for 2s.
    clock.advance(2_000)
    state = applyTrajectoryEvent(
      state,
      event('tool.complete', { result: { output: 'a' }, tool_id: 't1' }),
      clock.at,
    )

    // Step 2 begins when the tool finished — not when its first token lands.
    clock.advance(400)
    state = applyTrajectoryEvent(state, event('message.delta', { text: 'Found it.' }), clock.at)
    clock.advance(100)
    state = applyTrajectoryEvent(
      state,
      event('step.complete', { usage: { calls: 1, input: 30, output: 8, total: 38 } }),
      clock.at,
    )
    state = applyTrajectoryEvent(state, event('message.complete', { status: 'ok' }), clock.at)

    expect(kinds(state)).toEqual(['user', 'assistant', 'tool', 'assistant'])
    expect(assistants(state).map(record => record.step)).toEqual([1, 2])
    expect(tools(state)[0]?.durationMs).toBe(2_000)

    // Step 2's clock starts at the tool's completion, so its TTFT is 400ms —
    // the tool's 2s must not be charged to the model.
    expect(stepTtft(assistants(state)[1]?.metrics)).toBe('400 ms')
    expect(stepMetrics(assistants(state)[1]?.metrics).total).toBe('500 ms')
  })

  it('records a step that emitted only tool calls', () => {
    const clock = clockFrom(0)
    let state = recordPrompt(emptyTrajectory(), 'go', clock.at)

    clock.advance(200)
    state = applyTrajectoryEvent(
      state,
      event('step.complete', { usage: { calls: 1, input: 5, output: 2, total: 7 } }),
      clock.at,
    )
    state = applyTrajectoryEvent(
      state,
      event('tool.start', { args: { command: 'ls' }, name: 'terminal', tool_id: 't1' }),
      clock.at,
    )

    const step = assistants(state)[0]
    expect(step).toBeDefined()
    expect(step?.text).toBe('(tool calls only)')
    // No token was ever emitted, so TTFT has no honest value.
    expect(stepTtft(step?.metrics)).toBe('First token unavailable')
    expect(stepMetrics(step?.metrics).total).toBe('200 ms')
  })

  it('closes the step on tool.start when the backend reports no usage', () => {
    // An older backend sends no step.complete; the tool call is then the only
    // signal that the model finished emitting.
    const clock = clockFrom(0)
    let state = recordPrompt(emptyTrajectory(), 'go', clock.at)

    clock.advance(50)
    state = applyTrajectoryEvent(state, event('message.delta', { text: 'ok' }), clock.at)
    clock.advance(50)
    state = applyTrajectoryEvent(
      state,
      event('tool.start', { name: 'terminal', tool_id: 't1' }),
      clock.at,
    )

    expect(kinds(state)).toEqual(['user', 'assistant', 'tool'])
    expect(assistants(state)[0]?.endedAt).not.toBeNull()
    expect(stepMetrics(assistants(state)[0]?.metrics).throughput).toBe('Usage unavailable')
  })

  it('keeps parallel tools apart and resumes the clock at the last one', () => {
    const clock = clockFrom(0)
    let state = recordPrompt(emptyTrajectory(), 'go', clock.at)

    state = applyTrajectoryEvent(
      state,
      event('tool.start', { name: 'terminal', tool_id: 'a' }),
      clock.at,
    )
    state = applyTrajectoryEvent(
      state,
      event('tool.start', { name: 'read_file', tool_id: 'b' }),
      clock.at,
    )

    clock.advance(500)
    state = applyTrajectoryEvent(state, event('tool.complete', { tool_id: 'a' }), clock.at)
    clock.advance(500)
    state = applyTrajectoryEvent(state, event('tool.complete', { tool_id: 'b' }), clock.at)

    expect(tools(state).map(record => record.durationMs)).toEqual([500, 1_000])
    // The next step starts when the LAST tool finished, not the first.
    expect(state.pendingStepStartedAt).toBe(1_000)
  })
})

describe('failures', () => {
  it('marks a failed tool and keeps its reason', () => {
    const clock = clockFrom(0)
    let state = recordPrompt(emptyTrajectory(), 'go', clock.at)

    state = applyTrajectoryEvent(
      state,
      event('tool.start', { name: 'write_file', tool_id: 't1' }),
      clock.at,
    )
    state = applyTrajectoryEvent(
      state,
      event('tool.complete', { error: 'Error: denied', tool_id: 't1' }),
      clock.at,
    )

    expect(tools(state)[0]).toMatchObject({ error: 'Error: denied', isError: true })
  })

  it('appends a notice when the turn fails', () => {
    const clock = clockFrom(0)
    let state = recordPrompt(emptyTrajectory(), 'go', clock.at)

    state = applyTrajectoryEvent(
      state,
      event('message.complete', { error: 'overloaded', status: 'error' }),
      clock.at,
    )

    expect(kinds(state)).toEqual(['user', 'notice'])
    expect(state.records.at(-1)).toMatchObject({ isError: true, text: 'overloaded' })
  })

  it('ignores a completion for a tool it never saw start', () => {
    const state = applyTrajectoryEvent(
      emptyTrajectory(),
      event('tool.complete', { tool_id: 'ghost' }),
    )

    expect(state.records).toEqual([])
  })
})

describe('stats', () => {
  it('separates model time from tool time and averages only what was measured', () => {
    const clock = clockFrom(0)
    let state = recordPrompt(emptyTrajectory(), 'go', clock.at)

    // Step 1: 100ms TTFT, 900ms generating, 40 output tokens.
    clock.advance(100)
    state = applyTrajectoryEvent(state, event('message.delta', { text: 'a' }), clock.at)
    clock.advance(900)
    state = applyTrajectoryEvent(
      state,
      event('step.complete', {
        usage: { cache_read: 60, calls: 1, input: 100, output: 40, total: 140 },
      }),
      clock.at,
    )
    state = applyTrajectoryEvent(
      state,
      event('tool.start', { name: 'terminal', tool_id: 't1' }),
      clock.at,
    )
    clock.advance(3_000)
    state = applyTrajectoryEvent(state, event('tool.complete', { tool_id: 't1' }), clock.at)

    // Step 2: 300ms TTFT, 700ms generating, 60 output tokens.
    clock.advance(300)
    state = applyTrajectoryEvent(state, event('message.delta', { text: 'b' }), clock.at)
    clock.advance(700)
    state = applyTrajectoryEvent(
      state,
      event('step.complete', {
        usage: { cache_read: 140, calls: 1, input: 300, output: 60, total: 360 },
      }),
      clock.at,
    )
    state = applyTrajectoryEvent(state, event('message.complete', { status: 'ok' }), clock.at)

    const stats = trajectoryStats(state)
    expect(stats.turns).toBe(1)
    expect(stats.steps).toBe(2)
    expect(stats.llmMs).toBe(2_000)
    expect(stats.toolMs).toBe(3_000)
    expect(stats.ttftMs).toBe(200)
    // Input is the FULL prompt: billed input plus what came from cache
    // (100+60) + (300+140). Reporting only the billed part would make a
    // well-cached run look like it barely sent anything.
    expect(stats.inputTokens).toBe(600)
    expect(stats.outputTokens).toBe(100)
    // 100 output tokens over 1.6s of generation.
    expect(stats.throughput).toBeCloseTo(62.5, 1)
    // 200 cached of a 600-token prompt.
    expect(stats.cacheHitRatio).toBeCloseTo(1 / 3, 3)
  })

  it('reports no averages for an empty ledger rather than zeros', () => {
    const stats = trajectoryStats(emptyTrajectory())

    expect(stats.ttftMs).toBeNull()
    expect(stats.throughput).toBeNull()
    expect(stats.cacheHitRatio).toBeNull()
    expect(stats.steps).toBe(0)
  })
})

describe('formatting', () => {
  it('scales the unit to the magnitude', () => {
    expect(formatMs(null)).toBe('—')
    expect(formatMs(420)).toBe('420 ms')
    expect(formatMs(1_500)).toBe('1.50 s')
    expect(formatMs(95_000)).toBe('1m 35s')
  })

  it('names the argument that matters for a tool row', () => {
    expect(toolSummary('terminal', { command: 'ls -la' })).toBe('ls -la')
    expect(toolSummary('read_file', { file_path: '/a/b.ts' })).toBe('/a/b.ts')
    expect(toolSummary('search_files', { pattern: 'TODO' })).toBe('TODO')
    expect(toolSummary('mystery', {})).toBe('mystery')
  })
})

import { describe, expect, it } from 'vitest'

import type { TrajectoryRecord } from '../state/trajectory.ts'
import { deriveTimeline, timelineFocus } from './timeline.ts'

let counter = 0

function record(
  kind: TrajectoryRecord['kind'],
  startedAt: number | null,
  durationMs: number | null,
  turn = 1,
): TrajectoryRecord {
  counter += 1

  return {
    durationMs,
    endedAt: startedAt === null || durationMs === null ? null : startedAt + durationMs,
    id: `r${counter}`,
    index: counter,
    kind,
    startedAt,
    step: kind === 'assistant' ? 1 : 0,
    text: `${kind}`,
    turn,
  }
}

function reset() {
  counter = 0
}

describe('sequence projection', () => {
  it('gives every record equal width in order', () => {
    reset()
    const model = deriveTimeline(
      [record('user', 0, 0), record('assistant', 0, 5_000), record('tool', 0, 10)],
      'sequence',
    )

    expect(model?.spans.map(span => [span.start, span.end])).toEqual([
      [0, 1],
      [1, 2],
      [2, 3],
    ])
    // A 10ms tool stays as legible as a 5s model call — that is the point.
    expect(model?.end).toBe(3)
  })

  it('assigns one lane per role', () => {
    reset()
    const model = deriveTimeline(
      [record('user', 0, 0), record('assistant', 0, 1), record('tool', 0, 1)],
      'sequence',
    )

    expect(model?.spans.map(span => span.lane)).toEqual([0, 1, 2])
  })

  it('marks where each turn starts', () => {
    reset()
    const model = deriveTimeline(
      [record('user', 0, 0, 1), record('assistant', 0, 1, 1), record('user', 0, 0, 2)],
      'sequence',
    )

    expect(model?.turnMarks).toEqual([
      { time: 0, turn: 1 },
      { time: 2, turn: 2 },
    ])
  })

  it('returns null for an empty ledger', () => {
    expect(deriveTimeline([], 'sequence')).toBeNull()
  })
})

describe('duration projection', () => {
  it('uses real widths and removes the idle between operations', () => {
    reset()
    // Two 100ms operations 10 seconds apart: the gap is the user reading, not
    // the agent working, and left in it would squash both to invisibility.
    const model = deriveTimeline(
      [record('assistant', 1_000, 100), record('assistant', 11_000, 100)],
      'duration',
    )

    expect(model?.spans.map(span => [span.start, span.end])).toEqual([
      [1_000, 1_100],
      [1_100, 1_200],
    ])
  })

  it('keeps overlapping operations overlapping', () => {
    reset()
    // Parallel tools must not be compressed apart — their overlap IS the fact
    // worth seeing.
    const model = deriveTimeline(
      [record('tool', 0, 500), record('tool', 100, 500)],
      'duration',
    )

    expect(model?.spans.map(span => [span.start, span.end])).toEqual([
      [0, 500],
      [100, 600],
    ])
  })

  it('drops records that were never timed', () => {
    reset()
    const model = deriveTimeline([record('assistant', null, null), record('tool', 0, 10)], 'duration')

    expect(model?.spans).toHaveLength(1)
  })

  it('splits a model span at its first token', () => {
    reset()
    const step = record('assistant', 0, 1_000)
    step.metrics = { completedAt: 1_000, firstTokenAt: 250, startedAt: 0 }

    const model = deriveTimeline([step], 'duration')

    expect(model?.spans[0]?.ttftFraction).toBeCloseTo(0.25, 3)
  })

  it('omits the split when the timings are incomplete', () => {
    reset()
    const step = record('assistant', 0, 1_000)
    step.metrics = { completedAt: null, firstTokenAt: null, startedAt: 0 }

    expect(deriveTimeline([step], 'duration')?.spans[0]?.ttftFraction).toBeUndefined()
  })
})

describe('timelineFocus', () => {
  it('selects every record overlapping the range, inclusive', () => {
    reset()
    const model = deriveTimeline(
      [record('assistant', 0, 100), record('tool', 100, 100), record('tool', 300, 50)],
      'duration',
    )

    expect([...(timelineFocus(model, { end: 150, start: 50 }) ?? [])]).toEqual([1, 2])
  })

  it('is null without a selection, so nothing is dimmed', () => {
    reset()
    const model = deriveTimeline([record('tool', 0, 10)], 'duration')

    expect(timelineFocus(model, null)).toBeNull()
    expect(timelineFocus(null, { end: 1, start: 0 })).toBeNull()
  })
})

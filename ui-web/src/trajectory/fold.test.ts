import { describe, expect, it } from 'vitest'

import type { TrajectoryRecord } from '../state/trajectory.ts'
import { foldModel, visibleRecords } from './fold.ts'

let index = 0

function record(
  kind: TrajectoryRecord['kind'],
  turn: number,
  step: number,
): TrajectoryRecord {
  index += 1

  return {
    durationMs: null,
    endedAt: null,
    id: `r${index}`,
    index,
    kind,
    startedAt: null,
    step,
    text: `${kind} ${index}`,
    turn,
  }
}

/** user, step 1 + 2 tools, step 2 + 1 tool — one turn, then a second turn. */
function ledger(): TrajectoryRecord[] {
  index = 0

  return [
    record('user', 1, 0),
    record('assistant', 1, 1),
    record('tool', 1, 1),
    record('tool', 1, 1),
    record('assistant', 1, 2),
    record('tool', 1, 2),
    record('user', 2, 0),
    record('assistant', 2, 1),
  ]
}

describe('foldModel', () => {
  it('anchors turns on their prompt and steps on their request', () => {
    const model = foldModel(ledger())

    // Turn 1 hides its five following records; turn 2 hides one.
    expect(model.counts.get(1)).toBe(5)
    expect(model.counts.get(7)).toBe(1)
    // Steps hide only their own tool calls.
    expect(model.counts.get(2)).toBe(2)
    expect(model.counts.get(5)).toBe(1)
    expect(model.turnAnchors).toEqual([1, 7])
    expect(model.stepAnchors).toEqual([2, 5])
  })

  it('does not offer a fold that would hide nothing', () => {
    // A step with no tool calls is not foldable.
    const model = foldModel(ledger())

    expect(model.counts.has(8)).toBe(false)
  })

  it('marks the record that opens each turn', () => {
    expect([...foldModel(ledger()).turnStarts]).toEqual([1, 7])
  })
})

describe('visibleRecords', () => {
  it('shows everything when nothing is folded', () => {
    const records = ledger()

    expect(visibleRecords(records, new Set())).toHaveLength(records.length)
  })

  it('folding a turn keeps its prompt and hides the rest of that turn only', () => {
    const visible = visibleRecords(ledger(), new Set([1]))

    expect(visible.map(entry => entry.index)).toEqual([1, 7, 8])
  })

  it('folding a step hides its tool calls but not the next step', () => {
    const visible = visibleRecords(ledger(), new Set([2]))

    expect(visible.map(entry => entry.index)).toEqual([1, 2, 5, 6, 7, 8])
  })

  it('folds nest: a folded turn wins over a folded step inside it', () => {
    const visible = visibleRecords(ledger(), new Set([1, 2]))

    expect(visible.map(entry => entry.index)).toEqual([1, 7, 8])
  })

  it('keeps every anchor visible, so the numbering stays readable', () => {
    const visible = visibleRecords(ledger(), new Set([1, 2, 5, 7]))

    for (const anchor of [1, 7]) {
      expect(visible.some(entry => entry.index === anchor)).toBe(true)
    }
  })
})

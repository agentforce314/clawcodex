/**
 * Folding the ledger.
 *
 * Two nested groupings, each anchored on the record that owns it:
 *
 * - a **turn** is anchored on its user prompt and folds everything the agent
 *   did in response,
 * - a **step** is anchored on its model request and folds the tool calls that
 *   request asked for.
 *
 * Anchors stay visible when folded — a fold hides detail, never the fact that
 * something happened. That is what keeps `#N` numbering meaningful and lets a
 * collapsed view still be scanned for shape.
 */

import type { TrajectoryRecord } from '../state/trajectory.ts'

export interface FoldModel {
  /** Anchor index → how many records it hides when folded. */
  counts: Map<number, number>
  /** Anchors of the turn groups, in order. */
  turnAnchors: number[]
  /** Anchors of the step groups, in order. */
  stepAnchors: number[]
  /** Indexes that open a turn, for the ledger's turn marker. */
  turnStarts: Set<number>
}

/** Which records anchor a group, and how much each one hides. */
export function foldModel(records: readonly TrajectoryRecord[]): FoldModel {
  const counts = new Map<number, number>()
  const turnAnchors: number[] = []
  const stepAnchors: number[] = []
  const turnStarts = new Set<number>()
  const seenTurns = new Set<number>()

  let turnAnchor: number | null = null
  let stepAnchor: number | null = null

  for (const record of records) {
    if (!seenTurns.has(record.turn)) {
      seenTurns.add(record.turn)
      turnStarts.add(record.index)
    }

    if (record.kind === 'user') {
      turnAnchor = record.index
      stepAnchor = null
      counts.set(record.index, 0)
      turnAnchors.push(record.index)
      continue
    }

    // Everything after a prompt belongs to that turn's fold.
    if (turnAnchor !== null) counts.set(turnAnchor, (counts.get(turnAnchor) ?? 0) + 1)

    if (record.kind === 'assistant') {
      stepAnchor = record.index
      continue
    }

    if (record.kind === 'tool' && stepAnchor !== null) {
      if (!counts.has(stepAnchor)) stepAnchors.push(stepAnchor)

      counts.set(stepAnchor, (counts.get(stepAnchor) ?? 0) + 1)
    }
  }

  // An anchor that hides nothing is not foldable — a caret that does nothing
  // when clicked is worse than no caret.
  for (const [index, count] of [...counts]) {
    if (count === 0) counts.delete(index)
  }

  return {
    counts,
    stepAnchors: stepAnchors.filter(index => counts.has(index)),
    turnAnchors: turnAnchors.filter(index => counts.has(index)),
    turnStarts,
  }
}

/** Apply the folds: the records the ledger should actually render. */
export function visibleRecords(
  records: readonly TrajectoryRecord[],
  folded: ReadonlySet<number>,
): TrajectoryRecord[] {
  const visible: TrajectoryRecord[] = []
  let foldedTurn: number | null = null
  let foldedStep: { step: number; turn: number } | null = null

  for (const record of records) {
    if (foldedTurn !== null && record.turn !== foldedTurn) foldedTurn = null

    if (record.kind === 'user') {
      foldedStep = null
      visible.push(record)
      foldedTurn = folded.has(record.index) ? record.turn : null
      continue
    }

    if (foldedTurn !== null) continue

    if (record.kind === 'assistant') {
      visible.push(record)
      foldedStep = folded.has(record.index) ? { step: record.step, turn: record.turn } : null
      continue
    }

    if (
      record.kind === 'tool' &&
      foldedStep !== null &&
      foldedStep.turn === record.turn &&
      foldedStep.step === record.step
    ) {
      continue
    }

    visible.push(record)
  }

  return visible
}

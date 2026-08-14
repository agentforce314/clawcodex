/**
 * Projecting the ledger onto the timeline's horizontal axis.
 *
 * Two projections, answering two different questions:
 *
 * - `sequence` gives every operation the same width. It answers "what is the
 *   *shape* of this run" — how many steps, how many tools, in what order —
 *   and is readable even when one 40-second tool call dwarfs everything else.
 * - `duration` uses real elapsed widths, with idle time between operations
 *   removed. It answers "where did the time actually go". Idle is compressed
 *   because the gaps between turns are the user reading, not the agent
 *   working: left in, a conversation resumed after lunch renders as one
 *   sliver at the far right.
 *
 * Pure: the component below only paints what this returns.
 */

import type { TrajectoryKind, TrajectoryRecord } from '../state/trajectory.ts'

export type TimelineMode = 'duration' | 'sequence'

/** Lane 0 is what went in, lane 1 the model, lane 2 the tools it called. */
export const TIMELINE_LANES = ['Input', 'Model', 'Tools'] as const

export interface TimelineSpan {
  end: number
  index: number
  isError: boolean
  kind: TrajectoryKind
  label: string
  lane: number
  start: number
  /**
   * Share of a model span spent waiting for the first token, 0–1. Paints the
   * span two-tone, so a step that spent most of its time waiting looks
   * different from one that spent it generating.
   */
  ttftFraction?: number
}

export interface TimelineTurnMark {
  time: number
  turn: number
}

export interface TimelineModel {
  end: number
  spans: TimelineSpan[]
  start: number
  turnMarks: TimelineTurnMark[]
}

export interface TimeRange {
  end: number
  start: number
}

function laneFor(kind: TrajectoryKind): number {
  if (kind === 'tool') return 2
  if (kind === 'assistant') return 1

  return 0
}

function ttftFraction(record: TrajectoryRecord): number | undefined {
  const metrics = record.metrics

  if (metrics === undefined) return undefined

  const { completedAt, firstTokenAt, startedAt } = metrics

  if (startedAt === null || firstTokenAt === null || completedAt === null) return undefined

  const total = completedAt - startedAt

  if (total <= 0) return undefined

  return Math.min(1, Math.max(0, (firstTokenAt - startedAt) / total))
}

function baseSpan(record: TrajectoryRecord): Omit<TimelineSpan, 'end' | 'start'> {
  const fraction = record.kind === 'assistant' ? ttftFraction(record) : undefined

  return {
    index: record.index,
    isError: record.isError === true,
    kind: record.kind,
    label: record.text,
    lane: laneFor(record.kind),
    ...(fraction === undefined ? {} : { ttftFraction: fraction }),
  }
}

/** Project the ledger into the requested domain, or null when it is empty. */
export function deriveTimeline(
  records: readonly TrajectoryRecord[],
  mode: TimelineMode,
): TimelineModel | null {
  if (records.length === 0) return null

  return mode === 'sequence' ? sequenceTimeline(records) : durationTimeline(records)
}

function sequenceTimeline(records: readonly TrajectoryRecord[]): TimelineModel | null {
  const spans: TimelineSpan[] = []
  const turnMarks: TimelineTurnMark[] = []
  let lastTurn = 0

  for (const record of records) {
    if (record.turn !== lastTurn) {
      turnMarks.push({ time: spans.length, turn: record.turn })
      lastTurn = record.turn
    }

    spans.push({ ...baseSpan(record), end: spans.length + 1, start: spans.length })
  }

  if (spans.length === 0) return null

  return { end: spans.length, spans, start: 0, turnMarks }
}

function durationTimeline(records: readonly TrajectoryRecord[]): TimelineModel | null {
  const timed = records.filter(record => record.startedAt !== null)

  if (timed.length === 0) return null

  // Walk in start order, accumulating the idle removed before each span, so a
  // span's projected start is its real start minus every gap that preceded it.
  const ordered = [...timed].sort(
    (left, right) => (left.startedAt ?? 0) - (right.startedAt ?? 0) || left.index - right.index,
  )
  const removedBefore = new Map<number, number>()
  let removed = 0
  let coveredUntil: number | null = null

  for (const record of ordered) {
    const start = record.startedAt ?? 0
    const end = start + (record.durationMs ?? 0)

    if (coveredUntil !== null && start > coveredUntil) removed += start - coveredUntil

    removedBefore.set(record.index, removed)
    coveredUntil = coveredUntil === null ? end : Math.max(coveredUntil, end)
  }

  const spans: TimelineSpan[] = []
  const turnMarks: TimelineTurnMark[] = []
  let lastTurn = 0

  for (const record of timed) {
    const offset = removedBefore.get(record.index) ?? 0
    const start = (record.startedAt ?? 0) - offset
    const end = start + Math.max(0, record.durationMs ?? 0)

    if (record.turn !== lastTurn) {
      turnMarks.push({ time: start, turn: record.turn })
      lastTurn = record.turn
    }

    spans.push({ ...baseSpan(record), end, start })
  }

  return {
    end: Math.max(...spans.map(span => span.end)),
    spans,
    start: Math.min(...spans.map(span => span.start)),
    turnMarks,
  }
}

/** Ledger indexes touching an inclusive selection in the projected domain. */
export function timelineFocus(
  model: TimelineModel | null,
  range: TimeRange | null,
): ReadonlySet<number> | null {
  if (model === null || range === null) return null

  return new Set(
    model.spans
      .filter(span => span.start <= range.end && span.end >= range.start)
      .map(span => span.index),
  )
}

import { useCallback, useMemo, useState } from 'react'

import type { TrajectoryRecord, TrajectoryState } from '../state/trajectory.ts'
import { foldModel, visibleRecords } from './fold.ts'
import { deriveTimeline, timelineFocus, type TimeRange } from './timeline.ts'
import { TrajectoryDetails } from './TrajectoryDetails.tsx'
import { TrajectoryLedger } from './TrajectoryLedger.tsx'
import { TrajectoryTimeline } from './TrajectoryTimeline.tsx'
import { TrajectoryToolbar } from './TrajectoryToolbar.tsx'
import css from './TrajectoryView.module.css'

const EMPTY_SET: ReadonlySet<number> = new Set()

/** Everything a record's text could be matched against. */
function searchable(record: TrajectoryRecord): string {
  const parts = [record.text, record.detail ?? '', record.thinking ?? '', record.toolName ?? '']

  if (record.args !== undefined) {
    try {
      parts.push(JSON.stringify(record.args))
    } catch {
      /* non-serialisable args are matched by their summary alone */
    }
  }

  if (record.error !== undefined) parts.push(record.error)

  return parts.join(' ').toLowerCase()
}

export interface TrajectoryViewProps {
  trajectory: TrajectoryState
}

/**
 * The trajectory: what the agent actually did, as a metered ledger.
 *
 * Chat answers "what was said". This answers "what happened, in what order,
 * how long each part took, and what it cost" — the questions you have when a
 * run was slow, or expensive, or went somewhere you did not expect.
 */
export function TrajectoryView({ trajectory }: TrajectoryViewProps) {
  const [actualDuration, setActualDuration] = useState(false)
  const [folded, setFolded] = useState<ReadonlySet<number>>(EMPTY_SET)
  const [query, setQuery] = useState('')
  const [range, setRange] = useState<TimeRange | null>(null)
  const [selected, setSelected] = useState<number | null>(null)

  const records = trajectory.records
  const folds = useMemo(() => foldModel(records), [records])
  const visible = useMemo(() => visibleRecords(records, folded), [folded, records])

  // The timeline always shows the WHOLE run: folding is a reading aid for the
  // ledger, and a shape with pieces missing would misrepresent the run.
  const timeline = useMemo(
    () => deriveTimeline(records, actualDuration ? 'duration' : 'sequence'),
    [actualDuration, records],
  )

  const matchIndexes = useMemo(() => {
    const needle = query.trim().toLowerCase()

    if (needle === '') return null

    return new Set(
      records.filter(record => searchable(record).includes(needle)).map(record => record.index),
    )
  }, [query, records])

  const focusIndexes = useMemo(() => timelineFocus(timeline, range), [range, timeline])

  const allTurnsCollapsed =
    folds.turnAnchors.length > 0 && folds.turnAnchors.every(index => folded.has(index))
  const allStepsCollapsed =
    folds.stepAnchors.length > 0 && folds.stepAnchors.every(index => folded.has(index))

  const toggleFold = useCallback((record: TrajectoryRecord) => {
    setFolded(current => {
      const next = new Set(current)

      if (next.has(record.index)) next.delete(record.index)
      else next.add(record.index)

      return next
    })
  }, [])

  const toggleGroup = useCallback((anchors: number[], collapsed: boolean) => {
    setFolded(current => {
      const next = new Set(current)

      for (const index of anchors) {
        if (collapsed) next.delete(index)
        else next.add(index)
      }

      return next
    })
  }, [])

  const selectedRecord = useMemo(
    () => (selected === null ? undefined : records.find(record => record.index === selected)),
    [records, selected],
  )

  return (
    <div className={css.root}>
      <TrajectoryToolbar
        actualDuration={actualDuration}
        allStepsCollapsed={allStepsCollapsed}
        allTurnsCollapsed={allTurnsCollapsed}
        matchCount={matchIndexes === null ? null : matchIndexes.size}
        onActualDurationChange={next => {
          setActualDuration(next)
          setRange(null)
        }}
        onSearchQueryChange={setQuery}
        onToggleAllSteps={() => {
          toggleGroup(folds.stepAnchors, allStepsCollapsed)
        }}
        onToggleAllTurns={() => {
          toggleGroup(folds.turnAnchors, allTurnsCollapsed)
        }}
        searchQuery={query}
      />
      <TrajectoryTimeline
        matchIndexes={matchIndexes}
        model={timeline}
        onRangeChange={setRange}
        onSelect={setSelected}
        range={range}
        selectedIndex={selected}
      />
      <div className={css.panes}>
        <TrajectoryLedger
          focusIndexes={focusIndexes}
          foldCounts={folds.counts}
          foldedIndexes={folded}
          matchIndexes={matchIndexes}
          onSelect={setSelected}
          onToggleFold={toggleFold}
          records={visible}
          selectedIndex={selected}
          turnStarts={folds.turnStarts}
        />
        {selectedRecord !== undefined && (
          <TrajectoryDetails
            onClose={() => {
              setSelected(null)
            }}
            record={selectedRecord}
          />
        )}
      </div>
    </div>
  )
}

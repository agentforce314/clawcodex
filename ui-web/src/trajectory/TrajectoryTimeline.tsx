import { useCallback, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

import { TIMELINE_LANES, type TimelineModel, type TimeRange } from './timeline.ts'
import css from './TrajectoryTimeline.module.css'

export interface TrajectoryTimelineProps {
  matchIndexes: ReadonlySet<number> | null
  model: TimelineModel | null
  onRangeChange: (range: TimeRange | null) => void
  onSelect: (index: number) => void
  range: TimeRange | null
  selectedIndex: number | null
}

/** Drags shorter than this are a click on a span, not a brush. */
const DRAG_THRESHOLD_PX = 3

/**
 * The overview strip: every operation in the run, in three lanes.
 *
 * Doubles as a filter — dragging across it selects a time range and the ledger
 * below narrows to what was running then. That is the point of having it: on a
 * 200-record run, "the slow part" is a shape you can see and point at long
 * before it is something you could search for.
 */
export function TrajectoryTimeline({
  matchIndexes,
  model,
  onRangeChange,
  onSelect,
  range,
  selectedIndex,
}: TrajectoryTimelineProps) {
  const track = useRef<HTMLDivElement | null>(null)
  const origin = useRef<{ time: number; x: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  const domain = model === null ? 0 : Math.max(1e-6, model.end - model.start)

  const timeAt = useCallback(
    (clientX: number): number => {
      const element = track.current

      if (element === null || model === null) return 0

      const box = element.getBoundingClientRect()
      const ratio = box.width === 0 ? 0 : (clientX - box.left) / box.width

      return model.start + Math.min(1, Math.max(0, ratio)) * domain
    },
    [domain, model],
  )

  const percent = useCallback(
    (value: number): number => (model === null ? 0 : ((value - model.start) / domain) * 100),
    [domain, model],
  )

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (model === null) return

      event.currentTarget.setPointerCapture(event.pointerId)
      origin.current = { time: timeAt(event.clientX), x: event.clientX }
      setDragging(true)
    },
    [model, timeAt],
  )

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const start = origin.current

      if (start === null || !event.currentTarget.hasPointerCapture(event.pointerId)) return
      if (Math.abs(event.clientX - start.x) < DRAG_THRESHOLD_PX) return

      const time = timeAt(event.clientX)
      onRangeChange({ end: Math.max(start.time, time), start: Math.min(start.time, time) })
    },
    [onRangeChange, timeAt],
  )

  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const start = origin.current
      origin.current = null
      setDragging(false)

      if (!event.currentTarget.hasPointerCapture(event.pointerId)) return

      event.currentTarget.releasePointerCapture(event.pointerId)

      // A press with no travel clears the selection rather than creating an
      // empty one — the same gesture people use to dismiss a selection.
      if (start !== null && Math.abs(event.clientX - start.x) < DRAG_THRESHOLD_PX) {
        onRangeChange(null)
      }
    },
    [onRangeChange],
  )

  return (
    <div className={css.root}>
      <div className={css.plot}>
        <div className={css.labels}>
          {TIMELINE_LANES.map(lane => (
            <span key={lane}>{lane}</span>
          ))}
        </div>
        <div
          className={css.track}
          data-panning={dragging ? 'true' : undefined}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          ref={track}
        >
          {model === null ? (
            <div className={css.empty}>No activity recorded yet</div>
          ) : (
            <>
              {model.turnMarks.map(mark => (
                <div
                  className={css.turnMark}
                  key={`turn-${mark.turn}`}
                  style={{ left: `${percent(mark.time)}%` }}
                >
                  <span className={css.turnLabel}>T{mark.turn}</span>
                </div>
              ))}
              {model.spans.map(span => (
                <button
                  className={css.span}
                  data-error={span.isError ? 'true' : undefined}
                  data-kind={span.kind}
                  data-match={matchIndexes?.has(span.index) === true ? 'true' : undefined}
                  data-selected={span.index === selectedIndex ? 'true' : undefined}
                  data-ttft={span.ttftFraction === undefined ? undefined : ''}
                  key={span.index}
                  onClick={() => {
                    onSelect(span.index)
                  }}
                  style={
                    {
                      '--span-lane': span.lane,
                      '--span-left': `${percent(span.start)}%`,
                      '--span-ttft': `${(span.ttftFraction ?? 0) * 100}%`,
                      '--span-width': `${Math.max(0, percent(span.end) - percent(span.start))}%`,
                    } as React.CSSProperties
                  }
                  title={`#${span.index} ${span.label}`}
                  type="button"
                />
              ))}
              {range !== null && (
                <div
                  className={css.selection}
                  style={{
                    left: `${percent(range.start)}%`,
                    width: `${Math.max(0, percent(range.end) - percent(range.start))}%`,
                  }}
                />
              )}
            </>
          )}
          {range !== null && (
            <button
              className={css.clear}
              onClick={() => {
                onRangeChange(null)
              }}
              type="button"
            >
              Clear selection
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

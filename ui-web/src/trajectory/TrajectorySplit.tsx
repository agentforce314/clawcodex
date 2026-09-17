import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

import {
  $trajectoryDetailsWidth,
  setTrajectoryDetailsWidth,
  TRAJECTORY_DETAILS_DEFAULT,
} from '../state/layout.ts'
import css from './TrajectorySplit.module.css'

/** Own the resize state here so pointer moves do not re-render the ledger or inspector content. */
export function TrajectorySplit({ children, details }: { children: ReactNode; details: ReactNode }) {
  const preferred = useStore($trajectoryDetailsWidth)
  const root = useRef<HTMLDivElement>(null)
  const [available, setAvailable] = useState(0)
  const [dragging, setDragging] = useState(false)
  const paneId = useId()
  const gesture = useRef<{ pointerId: number; startX: number; width: number; moved: boolean } | null>(null)
  const latestX = useRef(0)
  const frame = useRef<number | null>(null)

  useLayoutEffect(() => {
    const element = root.current
    if (element === null) return

    const measure = () => setAvailable(element.getBoundingClientRect().width)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // Leave at least a quarter of the row for the ledger. A narrow viewport
  // may paint less than the preferred minimum without overwriting the preference.
  const max = available > 0 ? Math.floor(available * 0.75) : TRAJECTORY_DETAILS_DEFAULT
  const min = Math.min(240, max)
  const width = Math.min(max, Math.max(min, preferred))
  const limits = useRef({ min, max })
  limits.current = { min, max }

  const applyDrag = useCallback(() => {
    const active = gesture.current
    if (active === null) return
    if (latestX.current === active.startX && !active.moved) return
    active.moved = true

    const { min, max } = limits.current
    setTrajectoryDetailsWidth(Math.min(max, Math.max(min, active.width + active.startX - latestX.current)))
  }, [])

  const finish = useCallback(() => {
    if (gesture.current === null) return
    if (frame.current !== null) cancelAnimationFrame(frame.current)
    frame.current = null
    applyDrag()
    gesture.current = null
    setDragging(false)
  }, [applyDrag])

  useEffect(() => {
    if (!dragging) return
    window.addEventListener('blur', finish)
    return () => window.removeEventListener('blur', finish)
  }, [dragging, finish])

  useEffect(() => {
    if (details === null) finish()
  }, [details, finish])

  useEffect(() => () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current)
  }, [])

  return (
    <div className={css.root} data-dragging={dragging ? '' : undefined} ref={root}>
      {children}
      {details !== null && (
        <div className={css.details} id={paneId} style={{ width }}>
          <div
            aria-controls={paneId}
            aria-label="Resize trajectory details"
            aria-orientation="vertical"
            aria-valuemax={max}
            aria-valuemin={min}
            aria-valuenow={width}
            className={css.handle}
            onDoubleClick={() => setTrajectoryDetailsWidth(TRAJECTORY_DETAILS_DEFAULT)}
            onKeyDown={event => {
              const step = event.shiftKey ? 40 : 10
              const next = event.key === 'ArrowLeft' ? width + step
                : event.key === 'ArrowRight' ? width - step
                  : event.key === 'Home' ? min : event.key === 'End' ? max : null
              if (next === null) return
              event.preventDefault()
              setTrajectoryDetailsWidth(Math.min(max, Math.max(min, next)))
            }}
            onLostPointerCapture={finish}
            onPointerCancel={finish}
            onPointerDown={event => {
              if (event.button !== 0 || gesture.current !== null) return
              event.preventDefault()
              event.currentTarget.focus()
              event.currentTarget.setPointerCapture(event.pointerId)
              gesture.current = { pointerId: event.pointerId, startX: event.clientX, width, moved: false }
              latestX.current = event.clientX
              setDragging(true)
            }}
            onPointerMove={event => {
              if (gesture.current?.pointerId !== event.pointerId) return
              latestX.current = event.clientX
              frame.current ??= requestAnimationFrame(() => {
                frame.current = null
                applyDrag()
              })
            }}
            onPointerUp={event => {
              if (gesture.current?.pointerId !== event.pointerId) return
              latestX.current = event.clientX
              finish()
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                event.currentTarget.releasePointerCapture(event.pointerId)
              }
            }}
            role="separator"
            tabIndex={0}
            title="Drag to resize; double-click to reset"
          />
          {details}
        </div>
      )}
    </div>
  )
}

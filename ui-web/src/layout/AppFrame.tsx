import { useStore } from '@nanostores/react'
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'

import {
  $detailsWidth,
  $narrowExpanded,
  $sidebarWidth,
  setDetailsWidth,
  setNarrow,
  setSidebarWidth,
} from '../state/layout.ts'
import { computeColumns, SIDEBAR_AUTO_COLLAPSE, SIDEBAR_DEFAULT } from './columns.ts'
import css from './AppFrame.module.css'

interface DragHandleProps {
  left: number
  onDrag: (dx: number) => void
  onEnd: () => void
  onStart: () => void
  side: 'details' | 'sidebar'
}

/**
 * One drag handle: pointer capture plus rAF-throttled deltas measured against
 * the gesture's own origin, so a fast drag reports at most one width per frame
 * and never compounds.
 */
function DragHandle({ left, onDrag, onEnd, onStart, side }: DragHandleProps) {
  const [dragging, setDragging] = useState(false)
  const origin = useRef(0)
  const latest = useRef(0)
  const frame = useRef<number | null>(null)
  const callbacks = useRef({ onDrag, onEnd, onStart })
  callbacks.current = { onDrag, onEnd, onStart }

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    origin.current = event.clientX
    latest.current = event.clientX
    callbacks.current.onStart()
    setDragging(true)
  }, [])

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return

    latest.current = event.clientX
    frame.current ??= requestAnimationFrame(() => {
      frame.current = null
      callbacks.current.onDrag(latest.current - origin.current)
    })
  }, [])

  const onPointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return

    event.currentTarget.releasePointerCapture(event.pointerId)

    if (frame.current !== null) {
      cancelAnimationFrame(frame.current)
      frame.current = null
    }

    callbacks.current.onDrag(latest.current - origin.current)
    setDragging(false)
    callbacks.current.onEnd()
  }, [])

  return (
    <div
      className={css.handle}
      data-dragging={dragging ? '' : undefined}
      data-side={side}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      style={{ left }}
    />
  )
}

export interface AppFrameProps {
  conversation: ReactNode
  details: ReactNode
  /** Rendered with the resolved rail state, decided here by the solver. */
  sidebar: (state: { collapsed: boolean; width: number }) => ReactNode
}

/**
 * The three-column shell.
 *
 * It owns the grid tracks, the drag handles, and the concession solve. The
 * sidebar's collapsed state is decided *here* — from the viewport against the
 * breakpoint — so the solver in `columns.ts` stays breakpoint-free and the
 * stored preference is never rewritten by an automatic collapse.
 */
export function AppFrame({ conversation, details, sidebar }: AppFrameProps) {
  const sidebarPreference = useStore($sidebarWidth)
  const detailsPreference = useStore($detailsWidth)
  const narrowExpanded = useStore($narrowExpanded)

  const frameRef = useRef<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState(() =>
    typeof window === 'undefined' ? 1280 : window.innerWidth,
  )

  // Track the frame's own box rather than the window: an embedded shell can be
  // narrower than the window, and the breakpoint has to follow the box the
  // columns actually live in.
  useEffect(() => {
    const element = frameRef.current

    if (element === null) return

    let raf: number | null = null
    const observer = new ResizeObserver(() => {
      raf ??= requestAnimationFrame(() => {
        raf = null

        const width = element.getBoundingClientRect().width

        if (width > 0) setViewport(width)
      })
    })

    observer.observe(element)

    return () => {
      observer.disconnect()

      if (raf !== null) cancelAnimationFrame(raf)
    }
  }, [])

  const isNarrow = viewport < SIDEBAR_AUTO_COLLAPSE

  useEffect(() => {
    setNarrow(isNarrow)
  }, [isNarrow])

  const sidebarCollapsed = isNarrow ? !narrowExpanded : sidebarPreference === 0
  const effectiveSidebar = sidebarCollapsed
    ? 0
    : sidebarPreference === 0
      ? SIDEBAR_DEFAULT
      : sidebarPreference

  const cols = computeColumns(viewport, effectiveSidebar, detailsPreference)
  const colsRef = useRef(cols)
  colsRef.current = cols

  // The drag base is the RENDERED width captured at gesture start: grabbing a
  // concession-clamped panel must not jump back to the stored preference, and
  // freezing it for the whole gesture keeps deltas from compounding.
  const sidebarBase = useRef(0)
  const detailsBase = useRef(0)
  const [dragging, setDragging] = useState(false)

  const onDragEnd = useCallback(() => {
    setDragging(false)
  }, [])

  const onSidebarStart = useCallback(() => {
    sidebarBase.current = colsRef.current.sidebar
    setDragging(true)
  }, [])

  const onDetailsStart = useCallback(() => {
    detailsBase.current = colsRef.current.details
    setDragging(true)
  }, [])

  const onSidebarDrag = useCallback((dx: number) => {
    setSidebarWidth(sidebarBase.current + dx)
  }, [])

  const onDetailsDrag = useCallback((dx: number) => {
    setDetailsWidth(detailsBase.current - dx)
  }, [])

  return (
    <div
      className={css.frame}
      data-details-collapsed={cols.details === 0 ? '' : undefined}
      data-dragging={dragging ? '' : undefined}
      data-sidebar-collapsed={sidebarCollapsed ? '' : undefined}
      ref={frameRef}
      style={{ gridTemplateColumns: `${cols.sidebar}px minmax(0, 1fr) ${cols.details}px` }}
    >
      <div className={css.sidebarCol}>
        {sidebar({ collapsed: sidebarCollapsed, width: cols.sidebar })}
      </div>
      <div className={css.centerCol}>{conversation}</div>
      <div className={css.detailsCol}>{details}</div>
      {/* The collapsed rail is fixed-width: no resize handle while closed. */}
      {!sidebarCollapsed && (
        <DragHandle
          left={cols.sidebar}
          onDrag={onSidebarDrag}
          onEnd={onDragEnd}
          onStart={onSidebarStart}
          side="sidebar"
        />
      )}
      {cols.details > 0 && (
        <DragHandle
          left={viewport - cols.details}
          onDrag={onDetailsDrag}
          onEnd={onDragEnd}
          onStart={onDetailsStart}
          side="details"
        />
      )}
    </div>
  )
}

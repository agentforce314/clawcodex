import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $trajectoryDetailsWidth } from '../state/layout.ts'
import { TrajectorySplit } from './TrajectorySplit.tsx'

let available = 1000
let resized: ResizeObserverCallback

beforeEach(() => {
  vi.useFakeTimers()
  available = 1000
  window.localStorage.clear()
  $trajectoryDetailsWidth.set(320)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => (
    { width: available } as DOMRect
  ))
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resized = callback }
    observe() {}
    disconnect() {}
  })
})

afterEach(() => {
  cleanup()
  vi.runOnlyPendingTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function pointer(handle: HTMLElement, type: string, clientX: number) {
  const event = new MouseEvent(type, { bubbles: true, button: 0, clientX })
  Object.defineProperty(event, 'pointerId', { value: 1 })
  fireEvent(handle, event)
}

function divider() {
  const handle = screen.getByRole('separator', { name: 'Resize trajectory details' })
  Object.assign(handle, {
    setPointerCapture: vi.fn(), hasPointerCapture: () => true, releasePointerCapture: vi.fn(),
  })
  return handle
}

describe('trajectory details resizing', () => {
  it('drags left to grow the panel, commits the release position, and remembers the width', () => {
    const { rerender } = render(<TrajectorySplit details={<aside>Details</aside>}>Ledger</TrajectorySplit>)
    const handle = divider()
    pointer(handle, 'pointerdown', 680)
    pointer(handle, 'pointermove', 500)
    act(() => vi.advanceTimersByTime(20))
    expect(handle.getAttribute('aria-valuenow')).toBe('500')

    // Release before the next animation frame: keep the final pointer delta.
    pointer(handle, 'pointerup', 480)
    expect($trajectoryDetailsWidth.get()).toBe(520)
    expect(handle.parentElement?.style.width).toBe('520px')
    act(() => vi.advanceTimersByTime(200))
    expect(JSON.parse(window.localStorage.getItem('clawcodex.web.layout') ?? '{}').trajectoryDetails).toBe(520)

    rerender(<TrajectorySplit details={null}>Ledger</TrajectorySplit>)
    expect(screen.queryByRole('separator')).toBeNull()
    rerender(<TrajectorySplit details={<aside>Another record</aside>}>Ledger</TrajectorySplit>)
    expect(divider().getAttribute('aria-valuenow')).toBe('520')
  })

  it('can drag back to the original width', () => {
    render(<TrajectorySplit details={<aside>Details</aside>}>Ledger</TrajectorySplit>)
    const handle = divider()
    pointer(handle, 'pointerdown', 680)
    pointer(handle, 'pointermove', 500)
    act(() => vi.advanceTimersByTime(20))
    expect($trajectoryDetailsWidth.get()).toBe(500)
    pointer(handle, 'pointerup', 680)
    expect($trajectoryDetailsWidth.get()).toBe(320)
  })

  it('supports keyboard resizing, bounds, and resetting to the default', () => {
    render(<TrajectorySplit details={<aside>Details</aside>}>Ledger</TrajectorySplit>)
    const handle = divider()
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    expect($trajectoryDetailsWidth.get()).toBe(330)
    fireEvent.keyDown(handle, { key: 'ArrowRight', shiftKey: true })
    expect($trajectoryDetailsWidth.get()).toBe(290)
    fireEvent.keyDown(handle, { key: 'Home' })
    expect($trajectoryDetailsWidth.get()).toBe(240)
    fireEvent.keyDown(handle, { key: 'End' })
    expect($trajectoryDetailsWidth.get()).toBe(750)
    fireEvent.doubleClick(handle)
    expect($trajectoryDetailsWidth.get()).toBe(320)
  })

  it('fits a narrow pane and restores the preferred width when space returns', () => {
    $trajectoryDetailsWidth.set(600)
    render(<TrajectorySplit details={<aside>Details</aside>}>Ledger</TrajectorySplit>)
    const handle = divider()
    act(() => { available = 300; resized([], {} as ResizeObserver) })
    expect(handle.getAttribute('aria-valuenow')).toBe('225')
    // Grabbing without moving must not discard the wider preference.
    pointer(handle, 'pointerdown', 75)
    pointer(handle, 'pointerup', 75)
    expect($trajectoryDetailsWidth.get()).toBe(600)
    act(() => { available = 1000; resized([], {} as ResizeObserver) })
    expect(handle.getAttribute('aria-valuenow')).toBe('600')
  })

  it.each(['pointercancel', 'lostpointercapture', 'blur'])('ends dragging on %s', event => {
    const { container } = render(<TrajectorySplit details={<aside>Details</aside>}>Ledger</TrajectorySplit>)
    const handle = divider()
    pointer(handle, 'pointerdown', 680)
    pointer(handle, 'pointermove', 580)
    if (event === 'blur') fireEvent.blur(window)
    else pointer(handle, event, 580)
    expect(container.firstElementChild?.hasAttribute('data-dragging')).toBe(false)
    expect($trajectoryDetailsWidth.get()).toBe(420)
    pointer(handle, 'pointermove', 300)
    act(() => vi.advanceTimersByTime(20))
    expect($trajectoryDetailsWidth.get()).toBe(420)
  })

  it('keeps ledger and detail content out of per-pixel React updates', () => {
    const Ledger = vi.fn(() => <div>Ledger</div>)
    const Details = vi.fn(() => <aside>Details</aside>)
    render(<TrajectorySplit details={<Details />}><Ledger /></TrajectorySplit>)
    const before = [Ledger.mock.calls.length, Details.mock.calls.length]
    const handle = divider()
    pointer(handle, 'pointerdown', 680)
    pointer(handle, 'pointermove', 500)
    act(() => vi.advanceTimersByTime(20))
    pointer(handle, 'pointerup', 480)
    expect([Ledger.mock.calls.length, Details.mock.calls.length]).toEqual(before)
  })
})

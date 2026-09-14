import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $columnDrag, $detailsOpen, $detailsWidth, $sidebarWidth } from '../state/layout.ts'
import { AppFrame } from './AppFrame.tsx'

/** jsdom has no pointer capture; the handle only needs the calls to exist. */
function stubPointerCapture(): void {
  Object.assign(HTMLElement.prototype, {
    hasPointerCapture: () => true,
    releasePointerCapture: vi.fn(),
    setPointerCapture: vi.fn(),
  })
}

function renderFrame() {
  return render(
    <AppFrame
      conversation={<div>conversation</div>}
      details={<div>details</div>}
      sidebar={() => <div>sidebar</div>}
    />,
  )
}

beforeEach(() => {
  stubPointerCapture()
  window.localStorage.clear()
  $sidebarWidth.set(280)
  $detailsWidth.set(400)
})

afterEach(() => {
  cleanup()
  $detailsWidth.set(0)
})

describe('the details drag handle', () => {
  it('marks the frame while held, publishes the drag, and clears both on the release', () => {
    const { container } = renderFrame()
    const handle = container.querySelector('[data-side="details"]') as HTMLElement

    fireEvent.pointerDown(handle, { clientX: 900, pointerId: 1 })

    expect(container.firstElementChild?.hasAttribute('data-dragging')).toBe(true)
    expect($columnDrag.get()).toBe(true)

    fireEvent.pointerUp(handle, { clientX: 860, pointerId: 1 })

    expect(container.firstElementChild?.hasAttribute('data-dragging')).toBe(false)
    expect($columnDrag.get()).toBe(false)
  })

  it('tells open from closed without a word about the width', () => {
    // The store most of the app subscribes to changes only when the column
    // opens or closes, never on a pixel of a drag.
    const seen: boolean[] = []
    const off = $detailsOpen.subscribe(open => {
      seen.push(open)
    })

    $detailsWidth.set(420)
    $detailsWidth.set(480)
    $detailsWidth.set(0)
    $detailsWidth.set(360)
    off()

    expect(seen).toEqual([true, false, true])
  })

  it('ends the drag when the capture is lost, so a swallowed release cannot leave the column glued to the pointer', () => {
    const { container } = renderFrame()
    const handle = container.querySelector('[data-side="details"]') as HTMLElement

    fireEvent.pointerDown(handle, { clientX: 900, pointerId: 1 })
    fireEvent.lostPointerCapture(handle, { pointerId: 1 })

    expect(container.firstElementChild?.hasAttribute('data-dragging')).toBe(false)

    // A later move without a press must not resize anything.
    const before = $detailsWidth.get()

    fireEvent.pointerMove(handle, { clientX: 700, pointerId: 1 })

    expect($detailsWidth.get()).toBe(before)
  })

  it('ends the drag when the window loses focus', () => {
    const { container } = renderFrame()
    const handle = container.querySelector('[data-side="details"]') as HTMLElement

    fireEvent.pointerDown(handle, { clientX: 900, pointerId: 1 })
    fireEvent.blur(window)

    expect(container.firstElementChild?.hasAttribute('data-dragging')).toBe(false)
  })
})

import { describe, expect, it } from 'vitest'

import {
  CENTER_MIN,
  clampWidth,
  computeColumns,
  DETAILS_MAX,
  DETAILS_MIN,
  SIDEBAR_COLLAPSED,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
} from './columns.ts'

describe('clampWidth', () => {
  it('clamps into range and rounds', () => {
    expect(clampWidth(10, 100, 200)).toBe(100)
    expect(clampWidth(999, 100, 200)).toBe(200)
    expect(clampWidth(150.6, 100, 200)).toBe(151)
  })
})

describe('computeColumns', () => {
  it('honours both preferences when everything fits', () => {
    const cols = computeColumns(1600, 300, 400)

    expect(cols).toEqual({ center: 900, details: 400, sidebar: 300 })
  })

  it('resolves a closed sidebar to the fixed rail', () => {
    const cols = computeColumns(1600, 0, 0)

    expect(cols.sidebar).toBe(SIDEBAR_COLLAPSED)
    expect(cols.center).toBe(1600 - SIDEBAR_COLLAPSED)
  })

  it('shrinks details first to protect the centre floor', () => {
    // 1300 - 300 sidebar - 640 centre leaves 360 for details: above its floor,
    // so details concedes and the centre lands exactly on CENTER_MIN.
    const cols = computeColumns(1300, 300, 500)

    expect(cols.sidebar).toBe(300)
    expect(cols.center).toBe(CENTER_MIN)
    expect(cols.details).toBe(360)
    expect(cols.sidebar + cols.center + cols.details).toBe(1300)
  })

  it('closes details rather than take it below its floor', () => {
    // 1100 leaves only 160 after the sidebar and the centre floor — details
    // cannot shrink that far, so it closes and the centre takes the room.
    const cols = computeColumns(1100, 300, 500)

    expect(cols).toEqual({ center: 800, details: 0, sidebar: 300 })
  })

  it('auto-closes details when even its minimum will not fit', () => {
    const cols = computeColumns(900, 300, 400)

    expect(cols.details).toBe(0)
    expect(cols.sidebar).toBe(300)
    expect(cols.center).toBe(600)
  })

  it('never concedes the sidebar — the centre absorbs the deficit', () => {
    const cols = computeColumns(500, 300, 0)

    expect(cols.sidebar).toBe(300)
    expect(cols.center).toBe(200)
    expect(cols.details).toBe(0)
  })

  it('re-clamps preferences that cross the store boundary stale', () => {
    const cols = computeColumns(2000, 9999, 9999)

    expect(cols.sidebar).toBe(SIDEBAR_MAX)
    expect(cols.details).toBe(DETAILS_MAX)

    const narrow = computeColumns(2000, 10, 10)
    expect(narrow.sidebar).toBe(SIDEBAR_MIN)
    expect(narrow.details).toBe(DETAILS_MIN)
  })

  it('is pure: auto-close is derived, so re-widening restores the layout', () => {
    const preferences = [320, 480] as const
    const squeezed = computeColumns(900, ...preferences)
    const restored = computeColumns(1600, ...preferences)

    expect(squeezed.details).toBe(0)
    expect(restored.details).toBe(480)
    expect(restored).toEqual(computeColumns(1600, ...preferences))
  })

  it('never returns a negative width', () => {
    for (const viewport of [0, 1, 100, 320, 640, 1024, 1440, 2560]) {
      const cols = computeColumns(viewport, 300, 360)

      expect(cols.center).toBeGreaterThanOrEqual(0)
      expect(cols.details).toBeGreaterThanOrEqual(0)
      expect(cols.sidebar).toBeGreaterThanOrEqual(0)
    }
  })
})

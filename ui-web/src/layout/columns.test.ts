import { describe, expect, it } from 'vitest'

import {
  CENTER_MIN,
  clampWidth,
  computeColumns,
  DETAILS_DEFAULT_RATIO,
  DETAILS_MAX_RATIO,
  DETAILS_MIN,
  detailsDefault,
  detailsMax,
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
    // 1100 - 300 sidebar - 400 centre leaves 400 for details: above its floor,
    // so details concedes and the centre lands exactly on CENTER_MIN.
    const cols = computeColumns(1100, 300, 500)

    expect(cols.sidebar).toBe(300)
    expect(cols.center).toBe(CENTER_MIN)
    expect(cols.details).toBe(400)
    expect(cols.sidebar + cols.center + cols.details).toBe(1100)
  })

  it('closes details rather than take it below its floor', () => {
    // 950 leaves only 250 after the sidebar and the centre floor — details
    // cannot shrink that far, so it drops its track and the centre takes the room.
    const cols = computeColumns(950, 300, 500)

    expect(cols).toEqual({ center: 650, details: 0, sidebar: 300 })
  })

  it('lets details take most of a wide frame, and no more than its share', () => {
    // 4000 wide: the centre floor would allow 3300, but the column's share
    // of the frame is 2800, so that is where the drag stops.
    const cols = computeColumns(4000, 300, 3500)

    expect(cols.details).toBe(4000 * DETAILS_MAX_RATIO)
    expect(cols.center).toBe(4000 - 300 - 2800)
    expect(detailsMax(4000)).toBe(2800)
    // A frame too small for the share to reach the floor keeps the floor.
    expect(detailsMax(300)).toBe(DETAILS_MIN)
  })

  it('opens at its first-open share of the frame, inside the clamp', () => {
    expect(detailsDefault(1600)).toBe(1600 * DETAILS_DEFAULT_RATIO)
    expect(detailsDefault(500)).toBe(DETAILS_MIN)
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
    // 2000 wide: the share allows 1400, but the centre floor allows only
    // 2000 - 420 - 400 = 1180 beside the widest sidebar, so that binds.
    const cols = computeColumns(2000, 9999, 9999)

    expect(cols.sidebar).toBe(SIDEBAR_MAX)
    expect(cols.details).toBe(1180)
    expect(cols.center).toBe(CENTER_MIN)

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

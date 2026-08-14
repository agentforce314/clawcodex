import { describe, expect, it } from 'vitest'

import { absoluteTime, epochMs, relativeTime } from './recency.ts'

/** The reference "now" for every relative assertion. */
const ISO_NOW = '2026-08-14T12:00:00Z'
const NOW = Date.parse(ISO_NOW)

describe('epochMs', () => {
  it('reads the epoch-seconds float the live rows actually carry', () => {
    // The bug this module exists for: Date.parse(1786740090.6056044) is NaN,
    // so every live session showed a blank where its age should be.
    expect(epochMs(1_786_740_090.605_604_4)).toBe(1_786_740_090_605.604_4)
  })

  it('reads the ISO string the saved rows carry', () => {
    expect(epochMs(ISO_NOW)).toBe(NOW)
  })

  it('reads epoch milliseconds without multiplying them again', () => {
    // Guessing wrong by 1000x turns "3m ago" into "50 years ago".
    expect(epochMs(NOW)).toBe(NOW)
  })

  it('reads an epoch that survived JSON as a string', () => {
    expect(epochMs('1786740090.6056044')).toBe(1_786_740_090_605.604_4)
    expect(epochMs(String(NOW))).toBe(NOW)
  })

  it.each([null, undefined, '', '   ', 'not a date', 0, -5, Number.NaN, Number.POSITIVE_INFINITY])(
    'returns null for %p rather than a bogus instant',
    value => {
      expect(epochMs(value as number | string | null | undefined)).toBeNull()
    },
  )
})

describe('relativeTime', () => {
  it.each([
    [NOW, 'now'],
    [NOW - 30_000, 'now'],
    [NOW - 5 * 60_000, '5m'],
    [NOW - 3 * 3_600_000, '3h'],
    [NOW - 2 * 86_400_000, '2d'],
    [NOW - 20 * 86_400_000, '2w'],
  ])('renders %p as %s', (at, expected) => {
    expect(relativeTime(at, NOW)).toBe(expected)
  })

  it('renders an epoch-seconds row, which is the whole point', () => {
    expect(relativeTime(NOW / 1000 - 300, NOW)).toBe('5m')
  })

  it('shows a slightly-future row as "now" rather than a negative age', () => {
    // The backend stamps these; a browser clock a few seconds behind should
    // not produce "-1m".
    expect(relativeTime(NOW + 5000, NOW)).toBe('now')
  })

  it('renders nothing when there is no timestamp', () => {
    // An empty cell is honest; "unknown" repeated down the list is noise.
    expect(relativeTime(null, NOW)).toBe('')
    expect(relativeTime(undefined, NOW)).toBe('')
  })
})

describe('absoluteTime', () => {
  it('gives a full timestamp for the tooltip', () => {
    expect(absoluteTime(NOW)).toBe(new Date(NOW).toLocaleString())
  })

  it('gives nothing when there is no timestamp', () => {
    expect(absoluteTime(null)).toBe('')
  })
})

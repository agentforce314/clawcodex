import { describe, expect, it } from 'vitest'

import { headTailCap, splitByCap } from './head-tail-cap.ts'

describe('headTailCap', () => {
  it('shows everything under the cap', () => {
    expect(headTailCap(10, 40)).toEqual({ head: 10, hidden: 0, tail: 0 })
  })

  it('does not fold for a saving smaller than the slack', () => {
    // Hiding 3 lines behind a button costs more than it saves.
    expect(headTailCap(43, 40)).toEqual({ head: 43, hidden: 0, tail: 0 })
  })

  it('splits an over-cap count into head, fold, tail', () => {
    expect(headTailCap(100, 40)).toEqual({ head: 20, hidden: 60, tail: 20 })
  })

  it('gives the head the odd line', () => {
    expect(headTailCap(100, 41)).toEqual({ head: 21, hidden: 59, tail: 20 })
  })
})

describe('splitByCap', () => {
  const lines = Array.from({ length: 10 }, (_, index) => index)

  it('returns everything when the cap did not engage', () => {
    expect(splitByCap(lines, { head: 10, hidden: 0, tail: 0 })).toEqual({ head: lines, tail: [] })
  })

  it('windows the head and the tail', () => {
    expect(splitByCap(lines, { head: 2, hidden: 6, tail: 2 })).toEqual({
      head: [0, 1],
      tail: [8, 9],
    })
  })
})

import { beforeEach, describe, expect, it } from 'vitest'

import { $cronState, applyCronSnapshot, getCronState, resetCronState } from '../app/cronStore.js'

describe('cronStore', () => {
  beforeEach(() => resetCronState())

  it('hides the indicator for null/empty snapshots', () => {
    applyCronSnapshot(null)
    expect(getCronState()).toBeNull()

    applyCronSnapshot({ jobs: [], wakeup: null })
    expect(getCronState()).toBeNull()
  })

  it('folds jobs into a count plus the soonest fire (epoch s → ms)', () => {
    applyCronSnapshot({
      jobs: [
        { cron: '*/5 * * * *', id: 'aaaa1111', next_fire_at: 2_000_000_300 },
        { cron: '0 9 * * *', id: 'bbbb2222', next_fire_at: 2_000_000_100 }
      ],
      wakeup: null
    })
    expect(getCronState()).toEqual({
      jobCount: 2,
      nextJobAt: 2_000_000_100_000,
      wakeup: null
    })
  })

  it('carries the pending wakeup with reason and fallback flag', () => {
    applyCronSnapshot({
      jobs: [],
      wakeup: { fire_at: 2_000_000_060, is_fallback: true, reason: 'watching CI' }
    })
    expect(getCronState()).toEqual({
      jobCount: 0,
      nextJobAt: null,
      wakeup: { fireAt: 2_000_000_060_000, isFallback: true, reason: 'watching CI' }
    })
  })

  it('drops garbage fire times instead of rendering a 1970 countdown', () => {
    applyCronSnapshot({
      jobs: [{ cron: '* * * * *', id: 'cccc3333', next_fire_at: Number.NaN }],
      wakeup: { fire_at: 0, reason: 'bad' }
    })
    expect(getCronState()).toEqual({ jobCount: 1, nextJobAt: null, wakeup: null })
  })

  it('reset clears the atom', () => {
    applyCronSnapshot({ jobs: [], wakeup: { fire_at: 2_000_000_060, reason: 'r' } })
    expect($cronState.get()).not.toBeNull()
    resetCronState()
    expect($cronState.get()).toBeNull()
  })
})

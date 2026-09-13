import { describe, expect, it } from 'vitest'

import { formatClock } from './ChatView.tsx'

describe('formatClock', () => {
  it('reads seconds, then minutes, then hours, the smaller units padded', () => {
    expect(formatClock(15)).toBe('15s')
    expect(formatClock(125)).toBe('2m 05s')
    expect(formatClock(3599)).toBe('59m 59s')
    // The hour rolls at exactly 3600s, never at 60 displayed minutes.
    expect(formatClock(3600)).toBe('1h 00m 00s')
    expect(formatClock(3903)).toBe('1h 05m 03s')
  })
})

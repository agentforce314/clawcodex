import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { TrajectoryStats } from '../state/trajectory.ts'
import {
  billedInput,
  cacheHitPercent,
  formatCompactDuration,
  formatSpeed,
  StatsLine,
  statsGroups,
} from './StatsLine.tsx'

afterEach(cleanup)

const NO_RUN: TrajectoryStats = {
  cacheHitRatio: null,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  inputTokens: 0,
  llmMs: 0,
  outputTokens: 0,
  steps: 0,
  throughput: null,
  toolMs: 0,
  ttftMs: null,
  turns: 0,
  uncachedInputTokens: 0,
}

/** The reference strip's own example, figure for figure. */
const FULL_RUN: TrajectoryStats = {
  cacheHitRatio: 0.99,
  cacheReadTokens: 11_385_000,
  cacheWriteTokens: 50_000,
  inputTokens: 11_500_000,
  llmMs: 388_000,
  outputTokens: 65_900,
  steps: 106,
  throughput: 258,
  toolMs: 23_700,
  ttftMs: 1_300,
  turns: 2,
  uncachedInputTokens: 65_000,
}

describe('formatCompactDuration', () => {
  it('reads seconds to a tenth, minutes past that, hours past that', () => {
    expect(formatCompactDuration(23_700)).toBe('23.7s')
    expect(formatCompactDuration(1_300)).toBe('1.3s')
    expect(formatCompactDuration(388_000)).toBe('6m28s')
    expect(formatCompactDuration(7_500_000)).toBe('2h05m')
  })
})

describe('cacheHitPercent', () => {
  it('never rounds a partial hit up to a full one', () => {
    expect(cacheHitPercent(0.997)).toBe('99.7')
    expect(cacheHitPercent(0.9997)).toBe('99.97')
  })

  it('keeps a whole number whole, and a full hit at 100', () => {
    expect(cacheHitPercent(0.73)).toBe('73')
    expect(cacheHitPercent(1)).toBe('100')
  })
})

describe('formatSpeed', () => {
  it('keeps the digit that matters below 10 and drops it above', () => {
    expect(formatSpeed(3.46)).toBe('3.5')
    expect(formatSpeed(257.6)).toBe('258')
  })
})

describe('statsGroups', () => {
  it('reads as the reference strip, group for group', () => {
    expect(statsGroups(FULL_RUN)).toEqual([
      '2 turns · 106 steps',
      'LLM 6m28s · Tool call 23.7s',
      'TTFT avg 1.3s · 258 tok/s',
      'Cache hit 99%',
      'Input 11.5M tok · Output 65.9K tok',
    ])
    expect(billedInput(FULL_RUN)).toBe(11_500_000)
  })

  it('says "1 turn" and "1 step"', () => {
    expect(statsGroups({ ...FULL_RUN, steps: 1, turns: 1 })[0]).toBe('1 turn · 1 step')
  })

  it('is empty before the first step', () => {
    expect(statsGroups(NO_RUN)).toEqual([])
  })

  it('drops the speed group for a resumed run, which recorded no first-token times', () => {
    expect(statsGroups({ ...FULL_RUN, throughput: null, ttftMs: null })).toEqual([
      '2 turns · 106 steps',
      'LLM 6m28s · Tool call 23.7s',
      'Cache hit 99%',
      'Input 11.5M tok · Output 65.9K tok',
    ])
  })

  it('omits what was never measured instead of printing zeros', () => {
    expect(
      statsGroups({
        ...NO_RUN,
        llmMs: 0,
        outputTokens: 0,
        steps: 3,
        toolMs: 4_200,
        turns: 1,
      }),
    ).toEqual(['1 turn · 3 steps', 'Tool call 4.2s'])
  })

  it('shows the token group without a cache figure when nothing was cached', () => {
    expect(
      statsGroups({ ...NO_RUN, cacheHitRatio: 0, outputTokens: 120, steps: 1, turns: 1, uncachedInputTokens: 900 }),
    ).toEqual(['1 turn · 1 step', 'Cache hit 0%', 'Input 900 tok · Output 120 tok'])
  })
})

describe('StatsLine', () => {
  it('renders nothing before a turn', () => {
    const { container } = render(<StatsLine stats={NO_RUN} />)

    expect(container.firstChild).toBeNull()
  })

  it('separates the groups with pipes and carries the whole line as its title', () => {
    render(<StatsLine stats={FULL_RUN} />)

    const root = screen.getByTitle(
      '2 turns · 106 steps | LLM 6m28s · Tool call 23.7s | TTFT avg 1.3s · 258 tok/s | Cache hit 99% | Input 11.5M tok · Output 65.9K tok',
    )

    expect(root.textContent).toBe(
      '2 turns · 106 steps|LLM 6m28s · Tool call 23.7s|TTFT avg 1.3s · 258 tok/s|Cache hit 99%|Input 11.5M tok · Output 65.9K tok',
    )
    expect(screen.getByText('Cache hit 99%')).toBeTruthy()
  })
})

/**
 * costSummary — parity checks against the original's formatTotalCost
 * (cost-tracker.ts:194-265) and format helpers (utils/format.ts:34-131).
 * Expected strings are the original functions' outputs for the same inputs.
 */
import { describe, expect, it } from 'vitest'

import type { CostSnapshot } from '../gatewayTypes.js'
import {
  formatCost,
  formatDuration,
  formatNumber,
  formatTotalCost,
  getLastCostSnapshot,
  setLastCostSnapshot
} from '../lib/costSummary.js'

describe('formatCost', () => {
  it('uses 4 decimals at or below fifty cents (original boundary is > 0.5)', () => {
    expect(formatCost(0)).toBe('$0.0000')
    expect(formatCost(0.0567)).toBe('$0.0567')
    expect(formatCost(0.5)).toBe('$0.5000')
  })

  it('rounds to 2 decimals above fifty cents', () => {
    expect(formatCost(0.51)).toBe('$0.51')
    expect(formatCost(1.23456)).toBe('$1.23')
    expect(formatCost(12.999)).toBe('$13.00')
  })
})

describe('formatDuration', () => {
  it('formats sub-minute durations as floored whole seconds', () => {
    expect(formatDuration(0)).toBe('0s')
    expect(formatDuration(0.5)).toBe('0.0s')
    expect(formatDuration(999)).toBe('0s')
    expect(formatDuration(59_999)).toBe('59s')
  })

  it('formats minutes and carries 59.5s rounding into the next minute', () => {
    expect(formatDuration(60_000)).toBe('1m 0s')
    expect(formatDuration(119_500)).toBe('2m 0s')
    expect(formatDuration(83_000)).toBe('1m 23s')
  })

  it('formats hours and days', () => {
    expect(formatDuration(3_600_000)).toBe('1h 0m 0s')
    expect(formatDuration(3_723_000)).toBe('1h 2m 3s')
    expect(formatDuration(90_061_000)).toBe('1d 1h 1m')
  })
})

describe('formatNumber', () => {
  it('keeps small numbers uncompacted without forced decimals', () => {
    expect(formatNumber(0)).toBe('0')
    expect(formatNumber(900)).toBe('900')
  })

  it('compacts thousands with one consistent decimal, lowercased', () => {
    expect(formatNumber(1000)).toBe('1.0k')
    expect(formatNumber(1321)).toBe('1.3k')
    expect(formatNumber(100_500)).toBe('100.5k')
    expect(formatNumber(2_000_000)).toBe('2.0m')
  })
})

describe('formatTotalCost', () => {
  it('renders the original block for an empty session', () => {
    expect(formatTotalCost({})).toBe(
      'Total cost:            $0.0000\n' +
        'Total duration (API):  0s\n' +
        'Total duration (wall): 0s\n' +
        'Total code changes:    0 lines added, 0 lines removed\n' +
        'Usage:                 0 input, 0 output'
    )
  })

  it('renders per-model usage with cache read/write and singular line counts', () => {
    const snapshot: CostSnapshot = {
      model_usage: {
        'deepseek-chat': {
          cache_creation_input_tokens: 2000,
          cache_read_input_tokens: 100_500,
          cost_usd: 0.06,
          input_tokens: 1234,
          output_tokens: 3456
        }
      },
      total_api_duration_ms: 64_000,
      total_cost_usd: 0.06,
      total_duration_ms: 728_000,
      total_lines_added: 1,
      total_lines_removed: 2
    }

    expect(formatTotalCost(snapshot)).toBe(
      'Total cost:            $0.0600\n' +
        'Total duration (API):  1m 4s\n' +
        'Total duration (wall): 12m 8s\n' +
        'Total code changes:    1 line added, 2 lines removed\n' +
        'Usage by model:\n' +
        '       deepseek-chat:  1.2k input, 3.5k output, 100.5k cache read, 2.0k cache write ($0.0600)'
    )
  })

  it('renders web search requests when the backend reports them', () => {
    expect(
      formatTotalCost({
        model_usage: { m: { cost_usd: 0.01, input_tokens: 1, output_tokens: 1, web_search_requests: 2 } }
      })
    ).toContain('1 input, 1 output, 2 web search ($0.0100)')
  })

  it('appends the unknown-model caveat to the cost line', () => {
    expect(formatTotalCost({ has_unknown_model_cost: true, total_cost_usd: 1 })).toContain(
      '$1.00 (costs may be inaccurate due to usage of unknown models)'
    )
  })
})

describe('setLastCostSnapshot', () => {
  it('keeps the last non-empty snapshot and ignores empty riders', () => {
    setLastCostSnapshot({ total_cost_usd: 0.5 })
    setLastCostSnapshot({})
    setLastCostSnapshot(undefined)
    expect(getLastCostSnapshot()).toEqual({ total_cost_usd: 0.5 })
  })
})

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { TrajectoryStats } from '../state/trajectory.ts'
import { RunStatsBar } from './RunStatsBar.tsx'

afterEach(cleanup)

const EMPTY: TrajectoryStats = {
  cacheHitRatio: null,
  inputTokens: 0,
  llmMs: 0,
  outputTokens: 0,
  steps: 0,
  throughput: null,
  toolMs: 0,
  ttftMs: null,
  turns: 0,
}

const RAN: TrajectoryStats = {
  cacheHitRatio: 0.55,
  inputTokens: 30_100,
  llmMs: 7900,
  outputTokens: 640,
  steps: 2,
  throughput: 156,
  toolMs: 800,
  ttftMs: 1900,
  turns: 1,
}

const text = (): string => document.body.textContent ?? ''

describe('RunStatsBar', () => {
  it('renders nothing before a turn with no model to name', () => {
    // A bar holding nothing but separators is worse than no bar.
    const { container } = render(<RunStatsBar stats={EMPTY} />)

    expect(container.firstChild).toBeNull()
  })

  it('names the model before any figures exist', () => {
    // A fresh session still says what it is about to run on.
    render(<RunStatsBar model="deepseek-v4-pro" provider="deepseek" stats={EMPTY} />)

    expect(screen.getByText('deepseek:deepseek-v4-pro')).toBeTruthy()
    expect(text()).not.toMatch(/turn|LLM|tok/)
  })

  it('shows the model alone when the provider is unknown', () => {
    render(<RunStatsBar model="deepseek-v4-pro" stats={EMPTY} />)

    expect(screen.getByText('deepseek-v4-pro')).toBeTruthy()
  })

  it('reports the whole run once there is one', () => {
    render(<RunStatsBar model="deepseek-v4-pro" provider="deepseek" stats={RAN} />)

    const shown = text()

    expect(shown).toContain('1 turn')
    expect(shown).toContain('2 steps')
    expect(shown).toContain('LLM')
    expect(shown).toContain('Tools')
    expect(shown).toContain('TTFT avg')
    expect(shown).toContain('156')
    // Every pair in a group is separated; TTFT and throughput were the one
    // pair that ran together.
    expect(shown).toMatch(/TTFT avg 1\.90 s·156 tok\/s/)
    expect(shown).toContain('55%')
    expect(shown).toContain('30K')
  })

  it('keeps model time and tool time apart rather than summing them', () => {
    // "8.7s" says nothing actionable; "7.9s of model, 0.8s of tools" says
    // which half to go look at.
    render(<RunStatsBar stats={RAN} />)

    expect(text()).toMatch(/LLM 7\.90 s.*Tools 800 ms/)
  })

  it('omits figures that were never measured instead of printing zeros', () => {
    // A replayed session records no client-side timings; "TTFT 0ms" would be a
    // measurement that never happened.
    render(<RunStatsBar stats={{ ...RAN, cacheHitRatio: null, throughput: null, ttftMs: null }} />)

    const shown = text()

    expect(shown).not.toContain('TTFT')
    expect(shown).not.toContain('tok/s')
    expect(shown).not.toContain('Cache hit')
    expect(shown).toContain('1 turn')
  })

  it('says "1 turn" and "2 turns"', () => {
    const { rerender } = render(<RunStatsBar stats={RAN} />)
    expect(text()).toContain('1 turn')

    rerender(<RunStatsBar stats={{ ...RAN, steps: 1, turns: 2 }} />)
    expect(text()).toContain('2 turns')
    expect(text()).toContain('1 step')
  })
})

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SubagentEntry } from '../state/subagents.ts'
import { SubagentView } from './SubagentView.tsx'

const actions = vi.hoisted(() => ({
  fetchSubagentTranscript: vi.fn(async () => ({ agent_id: 'a1', found: false })),
  interruptSubagent: vi.fn(async () => {}),
}))

vi.mock('../state/actions.ts', () => actions)

const entry = (over: Partial<SubagentEntry> = {}): SubagentEntry => ({
  agentId: 'a1',
  key: 'k1',
  label: 'audit the store',
  prompt: 'Audit the store for leaks.',
  status: 'completed',
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(cleanup)

describe('SubagentView', () => {
  it('offers Stop in the seat while the run is going, and interrupts it', async () => {
    render(<SubagentView entry={entry({ status: 'running' })} />)

    await waitFor(() => {
      expect(actions.fetchSubagentTranscript).toHaveBeenCalledWith('a1')
    })
    expect(screen.getByText('This subagent is running.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))

    expect(actions.interruptSubagent).toHaveBeenCalledWith('a1')
  })

  it('is read-only once the run has settled', async () => {
    render(<SubagentView entry={entry({ report: 'All clear.' })} />)

    await waitFor(() => {
      expect(screen.getByText('This subagent is read-only.')).toBeTruthy()
    })
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
  })
})

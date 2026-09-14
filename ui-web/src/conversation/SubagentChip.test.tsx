import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $delegation, $sessionId } from '../state/store.ts'
import type { SubagentEntry } from '../state/subagents.ts'
import { SubagentChip } from './SubagentChip.tsx'

// The chip drives the gateway through these; the transport has its own tests.
const actions = vi.hoisted(() => ({
  fetchDelegationStatus: vi.fn(async () => {}),
  interruptSubagent: vi.fn(async () => {}),
  setDelegationPaused: vi.fn(async () => {}),
}))

vi.mock('../state/actions.ts', () => actions)

const entry = (over: Partial<SubagentEntry> = {}): SubagentEntry => ({
  key: 'k1',
  label: 'audit the store',
  status: 'completed',
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  $sessionId.set('s1')
  $delegation.set(null)
})

afterEach(() => {
  cleanup()
  $delegation.set(null)
  $sessionId.set(null)
})

describe('SubagentChip', () => {
  it('counts the delegations and the running ones', () => {
    render(
      <SubagentChip
        entries={[entry({ agentId: 'a1', status: 'running' }), entry({ key: 'k2', label: 'write the docs' })]}
        onOpen={vi.fn()}
        variant="count"
      />,
    )

    expect(screen.getByRole('button', { name: '2 subagents, 1 running' })).toBeTruthy()
  })

  it('reads the supervisor while open, offers Stop on running rows only, and keeps a Stop from opening the child', () => {
    $delegation.set({ active: [], max_concurrent_children: 4, paused: false })

    const onOpen = vi.fn()

    render(
      <SubagentChip
        entries={[
          entry({ agentId: 'a1', key: 'k1', label: 'audit the store', status: 'running' }),
          entry({ key: 'k2', label: 'write the docs' }),
        ]}
        onOpen={onOpen}
        variant="count"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '2 subagents, 1 running' }))

    expect(actions.fetchDelegationStatus).toHaveBeenCalledTimes(1)
    expect(screen.getByText('1 running of 4')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /^Stop / })).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Stop audit the store' }))

    expect(actions.interruptSubagent).toHaveBeenCalledWith('a1')
    expect(onOpen).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('write the docs'))

    expect(onOpen).toHaveBeenCalledWith('k2')
  })

  it('cannot stop a run that has not named itself yet', () => {
    render(<SubagentChip entries={[entry({ status: 'running' })]} onOpen={vi.fn()} variant="count" />)

    fireEvent.click(screen.getByRole('button', { name: '1 subagent, 1 running' }))

    const stop = screen.getByRole('button', { name: 'Stop audit the store' })

    expect(stop.hasAttribute('disabled')).toBe(true)
    // A missing cap is unknown, not zero.
    expect(screen.getByText('1 running')).toBeTruthy()
  })

  it('sends the pause state it wants, and reflects a paused session', () => {
    $delegation.set({ active: [], paused: false })

    render(<SubagentChip entries={[entry()]} onOpen={vi.fn()} variant="count" />)

    fireEvent.click(screen.getByRole('button', { name: '1 subagent' }))
    fireEvent.click(screen.getByRole('button', { name: 'Pause spawning' }))

    expect(actions.setDelegationPaused).toHaveBeenCalledWith(true)

    act(() => {
      $delegation.set({ active: [], paused: true })
    })

    expect(screen.getByRole('button', { name: 'Spawning paused' }).getAttribute('aria-pressed')).toBe('true')
  })
})

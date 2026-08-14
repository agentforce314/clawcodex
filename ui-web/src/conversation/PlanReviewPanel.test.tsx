import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PlanReviewPanel, updatesFor } from './PlanReviewPanel.tsx'

afterEach(cleanup)

const PLAN = '## Steps\n\n1. Rename the thing\n2. Update its callers'

describe('updatesFor', () => {
  it('maps "edit freely" to acceptEdits', () => {
    expect(updatesFor('auto')).toEqual([
      { destination: 'session', mode: 'acceptEdits', type: 'setMode' },
    ])
  })

  it('maps "ask before edits" to default', () => {
    expect(updatesFor('manual')).toEqual([
      { destination: 'session', mode: 'default', type: 'setMode' },
    ])
  })

  it('never offers bypassPermissions', () => {
    // Turning off every check is guarded by its own confirmation in the
    // approval-mode picker; a plan dialog must not smuggle it in.
    const modes = [...updatesFor('auto'), ...updatesFor('manual')].map(u => u.mode)

    expect(modes).not.toContain('bypassPermissions')
  })
})

describe('PlanReviewPanel', () => {
  it('renders the plan as markdown', () => {
    render(<PlanReviewPanel onApprove={vi.fn()} onReject={vi.fn()} plan={PLAN} />)

    expect(screen.getByRole('heading', { name: 'Steps' })).toBeTruthy()
    expect(screen.getByText('Rename the thing')).toBeTruthy()
  })

  it('says it is still loading rather than showing an empty plan', () => {
    // "No plan" and "not fetched yet" are different, and only one of them is
    // a reason to hesitate.
    render(<PlanReviewPanel onApprove={vi.fn()} onReject={vi.fn()} plan={null} />)

    expect(screen.getByText(/Loading the plan/)).toBeTruthy()
  })

  it('explains an empty plan instead of showing a blank card', () => {
    render(<PlanReviewPanel onApprove={vi.fn()} onReject={vi.fn()} plan="   " />)

    expect(screen.getByText(/wrote no plan file/)).toBeTruthy()
    // The decision is still the user's to make.
    expect(screen.getByRole('button', { name: 'Approve, edit freely' })).toBeTruthy()
  })

  it('offers both approvals and the way out', () => {
    render(<PlanReviewPanel onApprove={vi.fn()} onReject={vi.fn()} plan={PLAN} />)

    expect(screen.getByRole('button', { name: 'Keep planning' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approve, ask before edits' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approve, edit freely' })).toBeTruthy()
  })

  it('reports which approval was chosen', () => {
    const onApprove = vi.fn()
    render(<PlanReviewPanel onApprove={onApprove} onReject={vi.fn()} plan={PLAN} />)

    fireEvent.click(screen.getByRole('button', { name: 'Approve, ask before edits' }))
    expect(onApprove).toHaveBeenCalledWith('manual')
  })

  it('reports the free-editing approval separately', () => {
    const onApprove = vi.fn()
    render(<PlanReviewPanel onApprove={onApprove} onReject={vi.fn()} plan={PLAN} />)

    fireEvent.click(screen.getByRole('button', { name: 'Approve, edit freely' }))
    expect(onApprove).toHaveBeenCalledWith('auto')
  })

  it('keeps planning on Escape', () => {
    // The safe end of the decision: the agent stays read-only.
    const onReject = vi.fn()
    render(<PlanReviewPanel onApprove={vi.fn()} onReject={onReject} plan={PLAN} />)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onReject).toHaveBeenCalled()
  })

  it('cannot be approved twice by a double click', () => {
    // Both answers resolve the same parked ask; the second is a reply to
    // something that is no longer waiting.
    const onApprove = vi.fn()
    render(<PlanReviewPanel onApprove={onApprove} onReject={vi.fn()} plan={PLAN} />)

    const button = screen.getByRole('button', { name: 'Approve, edit freely' })
    fireEvent.click(button)
    fireEvent.click(button)

    expect(onApprove).toHaveBeenCalledTimes(1)
  })
})

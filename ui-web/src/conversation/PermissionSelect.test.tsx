import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PermissionSelect } from './PermissionSelect.tsx'

afterEach(cleanup)

function open(): void {
  fireEvent.click(screen.getByRole('button', { name: /Approval mode/ }))
}

describe('PermissionSelect', () => {
  it('lists the modes in increasing order of what runs unasked', () => {
    render(<PermissionSelect onChange={vi.fn()} value="manual" />)
    open()

    expect(screen.getAllByRole('menuitem').map(item => item.textContent)).toEqual([
      'Ask every timeEvery tool asks first',
      'Smart approvalsSafe tools run; risky ones ask',
      'Full accessNothing asks — including writes and shell',
    ])
  })

  it('switches a safe mode immediately', () => {
    const onChange = vi.fn()
    render(<PermissionSelect onChange={onChange} value="manual" />)
    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Smart approvals/ }))

    expect(onChange).toHaveBeenCalledWith('smart')
  })

  it('does not switch to full access on the click alone', () => {
    // The whole point of the guard: one click must not disable every
    // permission check for the session.
    const onChange = vi.fn()
    render(<PermissionSelect onChange={onChange} value="manual" />)
    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Full access/ }))

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('alertdialog')).toBeTruthy()
  })

  it('keeps the confirm disabled until the sentence is acknowledged', () => {
    const onChange = vi.fn()
    render(<PermissionSelect onChange={onChange} value="manual" />)
    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Full access/ }))

    const confirm = screen.getByRole('button', { name: 'Enable full access' })
    expect((confirm as HTMLButtonElement).disabled).toBe(true)

    fireEvent.click(screen.getByRole('checkbox'))
    expect((confirm as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(confirm)
    expect(onChange).toHaveBeenCalledWith('off')
  })

  it('cancelling leaves the mode alone', () => {
    const onChange = vi.fn()
    render(<PermissionSelect onChange={onChange} value="manual" />)
    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Full access/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('re-asks every time: the acknowledgement does not persist', () => {
    const onChange = vi.fn()
    render(<PermissionSelect onChange={onChange} value="manual" />)

    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Full access/ }))
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Full access/ }))
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false)
  })

  it('re-selecting the current mode is a no-op', () => {
    const onChange = vi.fn()
    render(<PermissionSelect onChange={onChange} value="smart" />)
    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Smart approvals/ }))

    expect(onChange).not.toHaveBeenCalled()
  })

  it('displays Full access while the session has not reported a mode', () => {
    // Sessions spawn in Full Access under the backend's implicit interactive
    // default, so the unknown-mode display must not claim something stricter.
    render(<PermissionSelect onChange={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Approval mode: Full access' })).toBeTruthy()
  })

  it('registers every pick while the mode is unknown, including Full access', () => {
    // With no session-reported mode the display is a guess, so a pick that
    // happens to match the guessed label must still reach onChange — a
    // deliberate pre-session "Full access" click goes through the full
    // confirmation ceremony rather than being swallowed as a no-op.
    const onChange = vi.fn()
    render(<PermissionSelect onChange={onChange} />)
    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Full access/ }))

    expect(screen.getByRole('alertdialog')).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Enable full access' }))
    expect(onChange).toHaveBeenCalledWith('off')
  })

  it('switches to a stricter mode immediately while the mode is unknown', () => {
    const onChange = vi.fn()
    render(<PermissionSelect onChange={onChange} />)
    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Ask every time/ }))

    expect(onChange).toHaveBeenCalledWith('manual')
  })

  it('marks full access on the resting chip', () => {
    // The chip is the only persistent reminder that nothing will be asked.
    render(<PermissionSelect onChange={vi.fn()} value="off" />)

    expect(screen.getByRole('button', { name: 'Approval mode: Full access' }).className).toMatch(
      /triggerDanger/,
    )
  })
})

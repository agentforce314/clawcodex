/**
 * The nano chip (backend `--nano`, docs/nano.md) on its two surfaces: the
 * composer row and the session tab.
 *
 * The rule under every case: the chip is driven by an explicit `true` and
 * nothing else — a backend that never says nano must never grow a badge.
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SessionTab } from '../sidebar-right/SessionTab.tsx'
import { $contextUsage, $sessionId, $transcript, $workspace } from '../state/store.ts'
import { emptyTranscript } from '../state/transcript.ts'
import { InputBar } from './InputBar.tsx'

afterEach(() => {
  cleanup()
  $transcript.set(emptyTranscript())
  $workspace.set('')
  $sessionId.set(null)
  $contextUsage.set(null)
})

function renderBar(nano?: boolean) {
  render(
    <InputBar
      draft=""
      effort={{ supported: false }}
      models={{}}
      nano={nano}
      onApprovalModeChange={vi.fn()}
      onDraftChange={vi.fn()}
      onEffortChange={vi.fn()}
      onModelChange={vi.fn()}
      onStop={vi.fn()}
      onSubmit={vi.fn()}
      running={false}
      usage={null}
    />,
  )
}

describe('InputBar nano chip', () => {
  it('renders the chip when the session is nano', () => {
    renderBar(true)

    expect(screen.getByText('nano')).toBeTruthy()
  })

  it('renders nothing by default', () => {
    // Absent on older backends must stay absent here — a chip with no flag
    // behind it would claim a mode the session is not in.
    renderBar()

    expect(screen.queryByText('nano')).toBeNull()
  })

  it('is a fact, not a control — no button role', () => {
    renderBar(true)

    const chip = screen.getByText('nano')

    expect(chip.tagName).toBe('SPAN')
    expect(chip.getAttribute('role')).toBeNull()
  })
})

describe('SessionTab harness row', () => {
  it('names the harness when the session is nano', () => {
    $transcript.set({
      ...emptyTranscript(),
      info: { model: 'deepseek-v4-flash', nano: true, provider: 'deepseek' },
    })

    render(<SessionTab />)

    expect(screen.getByText('Harness')).toBeTruthy()
    expect(screen.getByText('nano')).toBeTruthy()
  })

  it('shows no harness row for a default session', () => {
    // Default mode is not a fact worth a row — and strict === true keeps a
    // backend that never reported the field silent too.
    $transcript.set({
      ...emptyTranscript(),
      info: { model: 'deepseek-v4-flash', provider: 'deepseek' },
    })

    render(<SessionTab />)

    expect(screen.queryByText('Harness')).toBeNull()
  })
})

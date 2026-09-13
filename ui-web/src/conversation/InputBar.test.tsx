import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $commands } from '../state/store.ts'
import { InputBar } from './InputBar.tsx'

afterEach(cleanup)

beforeEach(() => {
  $commands.set([
    { description: 'Show available commands', name: '/help' },
    { description: 'Clear the conversation', name: '/clear' },
    { description: 'Compact the conversation to save context', name: '/compact' },
    { description: 'Enable plan mode', hint: '[<description>]', name: '/plan' },
  ])
})

function renderBar(approvalMode?: 'manual' | 'off' | 'smart', draft = '', onSubmit = vi.fn()) {
  return render(
    <InputBar
      approvalMode={approvalMode}
      draft={draft}
      effort={{ supported: false }}
      models={{}}
      onApprovalModeChange={vi.fn()}
      onDraftChange={vi.fn()}
      onEffortChange={vi.fn()}
      onModelChange={vi.fn()}
      onStop={vi.fn()}
      onSubmit={onSubmit}
      running={false}
      usage={null}
    />,
  )
}

describe('InputBar approval mode', () => {
  it('defaults to Full access before session.info reports a mode', () => {
    // Sessions spawn in Full Access (the backend's implicit interactive
    // default, same as the TUI), so the pre-session picker must not display
    // a stricter mode than the session will actually start in.
    renderBar()

    expect(
      screen.getByRole('button', { name: 'Approval mode: Full access' }),
    ).toBeTruthy()
  })

  it('shows the session-reported mode once known', () => {
    renderBar('manual')

    expect(
      screen.getByRole('button', { name: 'Approval mode: Ask every time' }),
    ).toBeTruthy()
  })
})

describe('InputBar command menu', () => {
  it('opens from the launcher with its two sections in usage order, and closes on Escape', () => {
    renderBar()

    fireEvent.click(screen.getByLabelText('Add files or run commands'))

    // Each row: the glyph, then the title. The image row comes first while the
    // model can read one; Plan follows it under Add, then the built-ins in
    // usage order, then the rest as the catalog lists them.
    const titles = screen
      .getAllByRole('option')
      .map(option => option.querySelectorAll('span')[1]?.textContent)

    expect(titles).toEqual(['Image', 'Plan', 'Compact', 'Clear', 'Help'])
    expect(screen.getByText('Add')).toBeTruthy()
    expect(screen.getByText('Commands')).toBeTruthy()

    fireEvent.keyDown(screen.getByLabelText('Message ClawCodex'), { key: 'Escape' })

    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('filters by what is typed after the slash and runs a bare command on Enter', () => {
    const onSubmit = vi.fn()

    renderBar(undefined, '/co', onSubmit)

    expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual([
      'CompactCompact the conversation to save context',
    ])

    fireEvent.keyDown(screen.getByLabelText('Message ClawCodex'), { key: 'Enter' })

    expect(onSubmit).toHaveBeenCalledWith('/compact')
  })
})

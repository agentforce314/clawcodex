import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { InputBar } from './InputBar.tsx'

afterEach(cleanup)

function renderBar(approvalMode?: 'manual' | 'off' | 'smart') {
  return render(
    <InputBar
      approvalMode={approvalMode}
      draft=""
      effort={{ supported: false }}
      models={{}}
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

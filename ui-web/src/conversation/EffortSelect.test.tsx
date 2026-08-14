import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EffortSelect, buildEffortMenu } from './EffortSelect.tsx'

afterEach(cleanup)

const LADDER = ['low', 'medium', 'high', 'xhigh', 'max']

function open(): void {
  fireEvent.click(screen.getByRole('button', { name: /Reasoning effort/ }))
}

describe('buildEffortMenu', () => {
  it('prepends auto, which the backend never sends', () => {
    // effort_options documents this: "levels never includes auto; the caller
    // prepends it, since 'let the provider decide' is meaningful exactly when
    // some real level is also on offer".
    expect(buildEffortMenu(LADDER).map(entry => ('id' in entry ? entry.id : ''))).toEqual([
      'auto',
      ...LADDER,
    ])
  })

  it('does not prepend auto twice if the backend ever starts sending it', () => {
    const ids = buildEffortMenu(['auto', 'low']).map(entry => ('id' in entry ? entry.id : ''))

    expect(ids).toEqual(['auto', 'low'])
  })

  it('offers nothing when there is no real rung', () => {
    // "Auto" alone is not a choice — it is the absence of one.
    expect(buildEffortMenu([])).toEqual([])
    expect(buildEffortMenu(['auto'])).toEqual([])
  })

  it('spells the rungs for a reader, xhigh included', () => {
    const labels = buildEffortMenu(LADDER).map(entry => ('label' in entry ? entry.label : ''))

    expect(labels).toEqual(['Auto', 'Low', 'Medium', 'High', 'X-High', 'Max'])
  })
})

describe('EffortSelect', () => {
  it('renders nothing when the model takes no effort parameter', () => {
    // The contract's own instruction: a picker whose every choice is silently
    // dropped is worse than no picker.
    const { container } = render(
      <EffortSelect onChange={vi.fn()} options={{ levels: LADDER, supported: false }} />,
    )

    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when supported is true but the ladder is empty', () => {
    const { container } = render(
      <EffortSelect onChange={vi.fn()} options={{ levels: [], supported: true }} />,
    )

    expect(container.firstChild).toBeNull()
  })

  it('shows the session’s current level on the chip', () => {
    render(
      <EffortSelect onChange={vi.fn()} options={{ current: 'high', levels: LADDER, supported: true }} />,
    )

    expect(screen.getByRole('button', { name: 'Reasoning effort: High' })).toBeTruthy()
  })

  it('treats an unset level as auto rather than blank', () => {
    // Nothing told the provider what to do, so the provider is deciding —
    // "Auto" describes what is actually happening.
    render(<EffortSelect onChange={vi.fn()} options={{ levels: LADDER, supported: true }} />)

    expect(screen.getByRole('button', { name: 'Reasoning effort: Auto' })).toBeTruthy()
  })

  it('reports the chosen level', () => {
    const onChange = vi.fn()
    render(
      <EffortSelect onChange={onChange} options={{ current: 'low', levels: LADDER, supported: true }} />,
    )
    open()
    fireEvent.click(screen.getByRole('menuitem', { name: /Max/ }))

    expect(onChange).toHaveBeenCalledWith('max')
  })

  it('re-selecting the current level is a no-op', () => {
    const onChange = vi.fn()
    render(
      <EffortSelect onChange={onChange} options={{ current: 'high', levels: LADDER, supported: true }} />,
    )
    open()
    // Anchored: an unanchored /High/ also matches the X-High rung.
    fireEvent.click(screen.getByRole('menuitem', { name: /^High/ }))

    expect(onChange).not.toHaveBeenCalled()
  })

  it('explains what each rung buys', () => {
    render(<EffortSelect onChange={vi.fn()} options={{ levels: LADDER, supported: true }} />)
    open()

    expect(screen.getByRole('menuitem', { name: /Let the provider decide/ })).toBeTruthy()
    expect(screen.getByRole('menuitem', { name: /Fastest, least thinking/ })).toBeTruthy()
  })
})

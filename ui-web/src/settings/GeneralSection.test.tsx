import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { GeneralSection, styleLabel } from './GeneralSection.tsx'

afterEach(cleanup)

const SETTINGS = {
  available_output_styles: ['default', 'explanatory'],
  language: '',
  output_style: 'default',
  recap: true,
}

function mount(overrides: Partial<Parameters<typeof GeneralSection>[0]> = {}) {
  const props = {
    approvalMode: 'manual' as const,
    onApproval: vi.fn(),
    onLanguage: vi.fn(),
    onRecap: vi.fn(),
    onStyle: vi.fn(),
    onTheme: vi.fn(),
    sessionLive: true,
    settings: SETTINGS,
    theme: 'system' as const,
    ...overrides,
  }

  render(<GeneralSection {...props} />)

  return props
}

describe('styleLabel', () => {
  it('title-cases the style name for its chip', () => {
    expect(styleLabel('default')).toBe('Default')
    expect(styleLabel('explanatory')).toBe('Explanatory')
  })
})

describe('GeneralSection', () => {
  it('marks the active theme and reports a switch', () => {
    const props = mount({ theme: 'dark' })

    expect(screen.getByRole('button', { name: 'Dark' }).getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: 'Light' }))
    expect(props.onTheme).toHaveBeenCalledWith('light')
  })

  it('keeps the theme row working with no session', () => {
    // Appearance belongs to this browser, not to a session.
    const props = mount({ sessionLive: false })

    fireEvent.click(screen.getByRole('button', { name: 'Dark' }))
    expect(props.onTheme).toHaveBeenCalledWith('dark')
  })

  it('disables the session rows until there is a session', () => {
    mount({ sessionLive: false })

    expect((screen.getByLabelText('Response language') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Explanatory' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
    expect((screen.getByRole('button', { name: 'Off' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getAllByText('Start a session to change this.')).toHaveLength(3)
  })

  it('keeps the approvals row working with no session', () => {
    // A pre-session choice is held and applied to the session that gets
    // created, so this row never waits for one.
    const props = mount({ approvalMode: undefined, sessionLive: false })

    fireEvent.click(screen.getByRole('button', { name: 'Ask every time' }))
    expect(props.onApproval).toHaveBeenCalledWith('manual')
  })

  it('saves a typed language, trimmed', () => {
    const props = mount()

    fireEvent.change(screen.getByLabelText('Response language'), { target: { value: ' Español ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(props.onLanguage).toHaveBeenCalledWith('Español')
  })

  it('offers Clear only when a language is set, and clears with an empty string', () => {
    // Empty is the wire contract for "follow my prompts".
    const props = mount({ settings: { ...SETTINGS, language: 'Español' } })

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(props.onLanguage).toHaveBeenCalledWith('')

    cleanup()
    mount()
    expect(screen.queryByRole('button', { name: 'Clear' })).toBeNull()
  })

  it('does not offer Save when nothing changed', () => {
    mount({ settings: { ...SETTINGS, language: 'English' } })

    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('marks the active output style and reports a switch', () => {
    const props = mount()

    expect(
      screen.getByRole('button', { name: 'Default' }).getAttribute('aria-pressed'),
    ).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: 'Explanatory' }))
    expect(props.onStyle).toHaveBeenCalledWith('explanatory')
  })

  it('re-clicking the active style is a no-op', () => {
    const props = mount()

    fireEvent.click(screen.getByRole('button', { name: 'Default' }))
    expect(props.onStyle).not.toHaveBeenCalled()
  })

  it('marks the active approval mode and switches a safe mode on one click', () => {
    const props = mount({ approvalMode: 'manual' })

    expect(
      screen.getByRole('button', { name: 'Ask every time' }).getAttribute('aria-pressed'),
    ).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: 'Smart approvals' }))
    expect(props.onApproval).toHaveBeenCalledWith('smart')
  })

  it('does not enable full access on the click alone', () => {
    // The whole point of the guard: one click must not disable every
    // permission check for this session and the ones after it.
    const props = mount({ approvalMode: 'manual' })

    fireEvent.click(screen.getByRole('button', { name: 'Full access' }))
    expect(props.onApproval).not.toHaveBeenCalled()

    const dialog = screen.getByRole('alertdialog')
    expect(dialog.textContent).toContain('default mode for new sessions')

    const confirm = screen.getByRole('button', { name: 'Enable full access' })
    expect((confirm as HTMLButtonElement).disabled).toBe(true)

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(confirm)
    expect(props.onApproval).toHaveBeenCalledWith('off')
  })

  it('cancelling the full-access confirmation leaves the mode alone', () => {
    const props = mount({ approvalMode: 'manual' })

    fireEvent.click(screen.getByRole('button', { name: 'Full access' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(props.onApproval).not.toHaveBeenCalled()
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('re-clicking a KNOWN current mode is a no-op, but an unknown one is not', () => {
    // While no mode has been reported the pressed chip is a guess (sessions
    // spawn in Full Access), so a deliberate pick that matches the guess must
    // still go through the confirmation rather than being swallowed.
    const props = mount({ approvalMode: 'off' })

    fireEvent.click(screen.getByRole('button', { name: 'Full access' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(props.onApproval).not.toHaveBeenCalled()

    cleanup()
    mount({ approvalMode: undefined })
    expect(
      screen.getByRole('button', { name: 'Full access' }).getAttribute('aria-pressed'),
    ).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: 'Full access' }))
    expect(screen.getByRole('alertdialog')).toBeTruthy()
  })

  it('marks the recap state and reports a flip', () => {
    const props = mount()

    expect(screen.getByRole('button', { name: 'On' }).getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: 'Off' }))
    expect(props.onRecap).toHaveBeenCalledWith(false)
  })

  it('re-clicking the active recap state is a no-op', () => {
    const props = mount({ settings: { ...SETTINGS, recap: false } })

    fireEvent.click(screen.getByRole('button', { name: 'Off' }))
    expect(props.onRecap).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'On' }))
    expect(props.onRecap).toHaveBeenCalledWith(true)
  })

  it('presses neither recap chip while the state is unreported', () => {
    // "off" and "unknown" must not look the same.
    const { recap: _, ...rest } = SETTINGS

    mount({ settings: rest })

    expect(screen.getByRole('button', { name: 'On' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'Off' }).getAttribute('aria-pressed')).toBe('false')
  })
})

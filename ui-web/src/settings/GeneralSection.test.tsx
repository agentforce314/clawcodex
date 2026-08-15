import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { GeneralSection, styleLabel } from './GeneralSection.tsx'

afterEach(cleanup)

const SETTINGS = {
  available_output_styles: ['default', 'explanatory'],
  language: '',
  output_style: 'default',
}

function mount(overrides: Partial<Parameters<typeof GeneralSection>[0]> = {}) {
  const props = {
    onLanguage: vi.fn(),
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
    expect(screen.getAllByText('Start a session to change this.')).toHaveLength(2)
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
})

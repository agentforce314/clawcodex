import { describe, expect, it } from 'vitest'

import { composerPromptText } from '../lib/prompt.js'

describe('composerPromptText', () => {
  it('returns shell prompt for ! commands', () => {
    expect(composerPromptText('❯', true)).toBe('$')
  })

  it('is the bare brand glyph — no provider prefix (the stats line carries it)', () => {
    expect(composerPromptText('❯')).toBe('❯')
    // Compound branding glyphs pass through unmodified.
    expect(composerPromptText('Ψ >')).toBe('Ψ >')
  })

  it('uses a Termux-safe ASCII prompt marker in normal mode', () => {
    expect(composerPromptText('❯', false, true)).toBe('>')
  })

  it('keeps shell mode ahead of Termux substitution', () => {
    expect(composerPromptText('❯', true, true)).toBe('$')
  })
})

import { describe, expect, it } from 'vitest'

import { shouldAcceptPlaceholderSuggestion } from '../app/useInputHandlers.js'
import { PLACEHOLDERS, suggestedQuery } from '../content/placeholders.js'

describe('suggestedQuery — extract the tab-acceptable query from a placeholder', () => {
  it('returns the quoted query verbatim', () => {
    expect(suggestedQuery('Try "explain this codebase"')).toBe('explain this codebase')
    expect(suggestedQuery('Try "fix the lint errors"')).toBe('fix the lint errors')
  })

  it('keeps trailing punctuation that is part of the query', () => {
    expect(suggestedQuery('Try "how does the config loader work?"')).toBe('how does the config loader work?')
  })

  it('extracts a suggested slash command, ignoring prose after the quotes', () => {
    expect(suggestedQuery('Try "/help" for commands')).toBe('/help')
  })

  it('takes the first quoted span when several appear', () => {
    expect(suggestedQuery('Try "first" or "second"')).toBe('first')
  })

  it('turns an open-ended stub into a continuable sentence (ellipsis → trailing space)', () => {
    expect(suggestedQuery('Try "write a test for…"')).toBe('write a test for ')
    expect(suggestedQuery('Try "write a test for..."')).toBe('write a test for ')
  })

  it('returns null when the placeholder carries no quoted query', () => {
    expect(suggestedQuery('Ask me anything…')).toBeNull()
    expect(suggestedQuery('')).toBeNull()
  })

  it('returns null when the quotes hold nothing but an ellipsis', () => {
    expect(suggestedQuery('Try "…"')).toBeNull()
    expect(suggestedQuery('Try "..."')).toBeNull()
  })

  it('every shipped placeholder yields null or a clean non-empty query', () => {
    const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

    for (const placeholder of PLACEHOLDERS) {
      const query = suggestedQuery(placeholder)

      if (query === null) {
        continue
      }

      expect(query.length).toBeGreaterThan(0)
      expect(query).not.toMatch(/(?:…|\.{3})$/)
      // Round-trip: the extracted query must sit in the placeholder as a
      // whole quoted span (optionally ellipsis-terminated). Bites when a
      // nested quote silently truncates the extraction.
      expect(placeholder).toMatch(new RegExp(`"${escape(query.trimEnd())}(?:…|\\.{3})?"`))
    }
  })

  it('the shipped list still contains tab-acceptable suggestions', () => {
    expect(PLACEHOLDERS.some(p => suggestedQuery(p) !== null)).toBe(true)
  })
})

describe('shouldAcceptPlaceholderSuggestion — Tab accepts only what is visibly suggested', () => {
  const visible = { busy: false, completionsLen: 0, conversationEmpty: true, input: '' }

  it('accepts plain Tab while the placeholder suggestion is showing', () => {
    expect(shouldAcceptPlaceholderSuggestion({ shift: false, tab: true }, visible)).toBe(true)
  })

  it('never fires for Shift+Tab — that cycles the permission mode', () => {
    expect(shouldAcceptPlaceholderSuggestion({ shift: true, tab: true }, visible)).toBe(false)
  })

  it('never fires for non-Tab keys', () => {
    expect(shouldAcceptPlaceholderSuggestion({ shift: false, tab: false }, visible)).toBe(false)
  })

  it('defers to an open completion menu', () => {
    expect(shouldAcceptPlaceholderSuggestion({ shift: false, tab: true }, { ...visible, completionsLen: 2 })).toBe(
      false
    )
  })

  it('stays inert once the user typed something (placeholder is hidden)', () => {
    expect(shouldAcceptPlaceholderSuggestion({ shift: false, tab: true }, { ...visible, input: 'h' })).toBe(false)
  })

  it('stays inert mid-conversation (placeholder is only shown on a fresh transcript)', () => {
    expect(
      shouldAcceptPlaceholderSuggestion({ shift: false, tab: true }, { ...visible, conversationEmpty: false })
    ).toBe(false)
  })

  it('stays inert while a turn is running (placeholder is hidden when busy)', () => {
    expect(shouldAcceptPlaceholderSuggestion({ shift: false, tab: true }, { ...visible, busy: true })).toBe(false)
  })
})

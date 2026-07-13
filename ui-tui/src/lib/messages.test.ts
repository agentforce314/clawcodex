import { describe, expect, it } from 'vitest'

import { appendTranscriptMessage, extractTag } from './messages.js'

describe('appendTranscriptMessage', () => {
  it('merges adjacent tool-only shelves into one transcript row', () => {
    const out = appendTranscriptMessage([{ kind: 'trail', role: 'system', text: '', tools: ['Terminal("one") ✓'] }], {
      kind: 'trail',
      role: 'system',
      text: '',
      tools: ['Terminal("two") ✓']
    })

    expect(out).toEqual([
      { kind: 'trail', role: 'system', text: '', tools: ['Terminal("one") ✓', 'Terminal("two") ✓'] }
    ])
  })

  it('merges tool shelves into the nearest thinking shelf', () => {
    const out = appendTranscriptMessage(
      [{ kind: 'trail', role: 'system', text: '', thinking: 'plan', tools: ['Terminal("one") ✓'] }],
      { kind: 'trail', role: 'system', text: '', tools: ['Terminal("two") ✓'] }
    )

    expect(out).toEqual([
      { kind: 'trail', role: 'system', text: '', thinking: 'plan', tools: ['Terminal("one") ✓', 'Terminal("two") ✓'] }
    ])
  })
})

// ── extractTag: exact port of the original (utils/messages.ts:635) ──────────

describe('extractTag', () => {
  it('extracts simple multiline tag content', () => {
    expect(extractTag('<tool_use_error>path is not a file: /x/src</tool_use_error>', 'tool_use_error')).toBe(
      'path is not a file: /x/src'
    )
    expect(extractTag('<e>line1\nline2</e>', 'e')).toBe('line1\nline2')
  })

  it('returns null when the tag is absent, empty, or unclosed', () => {
    expect(extractTag('no tags here', 'tool_use_error')).toBeNull()
    expect(extractTag('', 'tool_use_error')).toBeNull()
    expect(extractTag('<tool_use_error>unclosed', 'tool_use_error')).toBeNull()
    expect(extractTag('<tool_use_error></tool_use_error>', 'tool_use_error')).toBeNull()
  })

  it('handles attributes and case-insensitive matches', () => {
    expect(extractTag('<err code="1">boom</err>', 'err')).toBe('boom')
    expect(extractTag('<ERR>boom</ERR>', 'err')).toBe('boom')
  })

  it('only returns content at nesting depth zero', () => {
    // Outer match wins; the regex is non-greedy so the first top-level
    // closing tag ends the match (original behavior, preserved verbatim).
    expect(extractTag('<t>outer <t>inner</t></t>', 't')).toBe('outer <t>inner')
  })
})

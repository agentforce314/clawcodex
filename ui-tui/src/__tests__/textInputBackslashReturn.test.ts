import { describe, expect, it } from 'vitest'

import { applyBackslashReturn, lineNav } from '../components/textInput.js'

describe('applyBackslashReturn', () => {
  it('replaces the backslash before the caret with a newline, caret staying after it', () => {
    expect(applyBackslashReturn('hello\\', 6)).toEqual({ cursor: 6, value: 'hello\n' })
  })

  it('applies mid-string when the caret sits right after a backslash', () => {
    expect(applyBackslashReturn('ab\\cd', 3)).toEqual({ cursor: 3, value: 'ab\ncd' })
  })

  it('does not apply without a backslash directly before the caret', () => {
    expect(applyBackslashReturn('hello', 5)).toBeNull()
    expect(applyBackslashReturn('a\\b', 3)).toBeNull()
    expect(applyBackslashReturn('', 0)).toBeNull()
  })

  it('does not apply when the caret is at the start', () => {
    expect(applyBackslashReturn('\\rest', 0)).toBeNull()
  })
})

describe('multi-line value produced by backslash+Enter stays editable', () => {
  it('up-arrow reaches the first line (regression: read-only continuation rows)', () => {
    // 'line1\' + Enter, then 'line2' typed on the new line.
    const cont = applyBackslashReturn('line1\\', 6)!
    const value = cont.value + 'line2'
    const cursorAtEnd = value.length

    // lineNav must cross the boundary that backslash+Enter created.
    expect(lineNav(value, cursorAtEnd, -1)).toBe(5)
    // ...and refuse only on the real first line, where history takes over.
    expect(lineNav(value, 0, -1)).toBeNull()
  })

  it('backspace at the start of line 2 can merge lines (newline is in-buffer)', () => {
    const cont = applyBackslashReturn('line1\\', 6)!
    const value = cont.value + 'line2'
    const startOfLine2 = value.indexOf('\n') + 1

    // The newline is an ordinary in-buffer character; deleting it merges the
    // lines. Under the old inputBuf model there was nothing to delete here.
    const merged = value.slice(0, startOfLine2 - 1) + value.slice(startOfLine2)

    expect(value[startOfLine2 - 1]).toBe('\n')
    expect(merged).toBe('line1line2')
  })
})

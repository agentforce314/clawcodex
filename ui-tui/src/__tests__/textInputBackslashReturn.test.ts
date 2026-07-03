import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

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

// The Enter handler's dispatch order lives inside useInput and cannot be
// exercised without mounting the renderer + stdin, so — same convention as
// textInputCursorSourceOfTruth.test.ts — pin the contract at the source
// level instead.
const TEXT_INPUT_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', 'components', 'textInput.tsx')
const source = readFileSync(TEXT_INPUT_PATH, 'utf8')

describe('k.return handler contract (source pin)', () => {
  it('gates the continuation on the multiline prop, so plain TextInputs still submit trailing backslashes', () => {
    expect(source).toMatch(/multiline\s*\?\s*applyBackslashReturn\(/)
  })

  it('checks continuation BEFORE the modifier-chord newline branch, mirroring original useTextInput.handleEnter', () => {
    const returnHandler = source.slice(source.indexOf('if (k.return)'))
    const continuationAt = returnHandler.indexOf('applyBackslashReturn(')
    const chordBranchAt = returnHandler.indexOf('k.shift || k.ctrl')

    expect(continuationAt).toBeGreaterThan(-1)
    expect(chordBranchAt).toBeGreaterThan(-1)
    expect(continuationAt).toBeLessThan(chordBranchAt)
  })

  it('consumes the continuation (early return) instead of falling through to submit', () => {
    expect(source).toMatch(/if\s*\(continuation\)\s*\{\s*commit\(continuation\.value,\s*continuation\.cursor\)\s*return\s*\}/)
  })
})

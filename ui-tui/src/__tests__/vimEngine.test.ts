import { describe, expect, it } from 'vitest'

import {
  applyMotion,
  dispatchNormal,
  initialVimState,
  resolveMotion,
  type Buffer,
  type VimState,
} from '../vim/engine.js'

// Helper: run a sequence of keys through the NORMAL-mode dispatcher.
function run(value: string, cursor: number, keys: string[]): { state: VimState; buffer: Buffer } {
  let state = initialVimState()
  let buffer: Buffer = { value, cursor }
  for (const k of keys) {
    const r = dispatchNormal(state, buffer, k)
    state = r.state
    buffer = r.buffer
  }
  return { state, buffer }
}

describe('motions', () => {
  const v = 'hello world foo'
  it('h/l move by one char, clamped to the line', () => {
    expect(resolveMotion('l', v, 0)).toBe(1)
    expect(resolveMotion('h', v, 0)).toBe(0) // clamped at start
    expect(resolveMotion('h', v, 5)).toBe(4)
  })
  it('w jumps to next word start', () => {
    expect(resolveMotion('w', v, 0)).toBe(6) // 'hello' -> 'world'
    expect(resolveMotion('w', v, 6)).toBe(12) // 'world' -> 'foo'
  })
  it('b jumps to previous word start', () => {
    expect(resolveMotion('b', v, 12)).toBe(6)
    expect(resolveMotion('b', v, 6)).toBe(0)
  })
  it('e jumps to end of word (inclusive)', () => {
    expect(resolveMotion('e', v, 0)).toBe(4) // last char of 'hello'
    expect(resolveMotion('e', v, 6)).toBe(10) // last char of 'world'
  })
  it('0 and $ go to line ends', () => {
    expect(resolveMotion('0', v, 8)).toBe(0)
    expect(resolveMotion('$', v, 0)).toBe(v.length - 1)
  })
  it('w treats punctuation as its own word', () => {
    const p = 'a.b c'
    expect(resolveMotion('w', p, 0)).toBe(1) // 'a' -> '.'
    expect(resolveMotion('w', p, 1)).toBe(2) // '.' -> 'b'
    expect(resolveMotion('W', p, 0)).toBe(4) // WORD: 'a.b' -> 'c'
  })
  it('applyMotion applies count and stops when stuck', () => {
    expect(applyMotion('w', v, 0, 2)).toBe(12) // hello -> world -> foo
    expect(applyMotion('h', v, 2, 10)).toBe(0) // clamps, stops
  })
})

describe('multiline motions', () => {
  const v = 'abc\ndef\nghi'
  it('j/k move between lines preserving column', () => {
    expect(resolveMotion('j', v, 1)).toBe(5) // col 1 on line 2 => 'e'
    expect(resolveMotion('k', v, 5)).toBe(1)
  })
  it('0/$ operate within the logical line', () => {
    expect(resolveMotion('0', v, 5)).toBe(4) // start of line 2
    expect(resolveMotion('$', v, 4)).toBe(6) // 'f'
  })
  it('G goes to last line, gg to top', () => {
    expect(resolveMotion('G', v, 0)).toBe(8) // start of 'ghi'
    expect(resolveMotion('gg', v, 8)).toBe(0)
  })
})

describe('mode transitions', () => {
  it('i/a/I/A enter insert mode at the right offset', () => {
    expect(run('hello', 2, ['i']).state.mode).toBe('insert')
    expect(run('hello', 2, ['a']).buffer.cursor).toBe(3)
    expect(run('  hi', 3, ['I']).buffer.cursor).toBe(2) // first non-blank
    expect(run('hello', 0, ['A']).buffer.cursor).toBe(5) // end of line
  })
  it('o/O open a new line and enter insert', () => {
    const o = run('abc', 1, ['o'])
    expect(o.buffer.value).toBe('abc\n')
    expect(o.buffer.cursor).toBe(4)
    expect(o.state.mode).toBe('insert')
    const bigO = run('abc', 1, ['O'])
    expect(bigO.buffer.value).toBe('\nabc')
    expect(bigO.buffer.cursor).toBe(0)
  })
})

describe('operators', () => {
  it('x deletes the char under the cursor', () => {
    expect(run('hello', 1, ['x']).buffer.value).toBe('hllo')
  })
  it('x with count deletes multiple, bounded by line', () => {
    expect(run('hello', 0, ['3', 'x']).buffer.value).toBe('lo')
  })
  it('dw deletes to next word', () => {
    const r = run('hello world', 0, ['d', 'w'])
    expect(r.buffer.value).toBe('world')
    expect(r.state.mode).toBe('normal')
  })
  it('cw acts like ce (changes to word END, keeps trailing space) — :help cw', () => {
    const r = run('hello world', 0, ['c', 'w'])
    expect(r.buffer.value).toBe(' world') // NOT 'world' — cw ≠ dw
    expect(r.state.mode).toBe('insert')
  })
  it('cw on whitespace behaves like dw (no special case)', () => {
    // cursor on the space between words → cw deletes the blank run + next word start
    const r = run('a  b', 1, ['c', 'w'])
    expect(r.state.mode).toBe('insert')
  })
  it('de deletes to end of word (inclusive)', () => {
    expect(run('hello world', 0, ['d', 'e']).buffer.value).toBe(' world')
  })
  it('d$ / D delete to end of line', () => {
    expect(run('hello world', 5, ['d', '$']).buffer.value).toBe('hello')
    expect(run('hello world', 5, ['D']).buffer.value).toBe('hello')
  })
  it('dd deletes the whole line', () => {
    expect(run('abc\ndef\nghi', 5, ['d', 'd']).buffer.value).toBe('abc\nghi')
  })
  it('2dw deletes two words', () => {
    expect(run('one two three', 0, ['2', 'd', 'w']).buffer.value).toBe('three')
  })
  it('an invalid motion cancels a pending operator', () => {
    const r = run('hello', 0, ['d', 'z'])
    expect(r.buffer.value).toBe('hello') // unchanged
    expect(r.state.pendingOperator).toBeNull()
  })
})

describe('count prefix', () => {
  it('accumulates multi-digit counts', () => {
    // 12l would move 12 right but line is short → clamps to last char
    const r = run('abcdefghijklmno', 0, ['1', '2', 'l'])
    expect(r.buffer.cursor).toBe(12)
    expect(r.state.count).toBe(0) // reset after use
  })
  it('0 is a motion when no count is in progress', () => {
    expect(run('hello', 3, ['0']).buffer.cursor).toBe(0)
  })
})

describe('linewise operators (critic MAJOR-2/-3)', () => {
  const v = 'L1\nL2\nL3\nL4'
  it('dj deletes the current AND next line', () => {
    // cursor on L2 (offset 3), dj → delete L2+L3
    expect(run(v, 3, ['d', 'j']).buffer.value).toBe('L1\nL4')
  })
  it('dk deletes the current AND previous line', () => {
    expect(run(v, 6, ['d', 'k']).buffer.value).toBe('L1\nL4') // on L3, dk → L2+L3
  })
  it('dG deletes from the current line to the last', () => {
    expect(run(v, 3, ['d', 'G']).buffer.value).toBe('L1') // L2..L4 gone
  })
  it('dgg deletes from the first line to the current', () => {
    expect(run(v, 6, ['d', 'g', 'g']).buffer.value).toBe('L4') // L1..L3 gone
  })
  it('dd on the LAST line removes the preceding newline (no trailing empty line)', () => {
    expect(run('abc\ndef', 4, ['d', 'd']).buffer.value).toBe('abc') // not 'abc\n'
  })
  it('2dd deleting the last two lines leaves no trailing newline', () => {
    expect(run('L1\nL2\nL3', 3, ['2', 'd', 'd']).buffer.value).toBe('L1')
  })
  it('dd on a single-line buffer empties it', () => {
    expect(run('only', 0, ['d', 'd']).buffer.value).toBe('')
  })
})

describe('gg / G through the dispatcher', () => {
  it('gg moves to the top', () => {
    expect(run('a\nb\nc', 4, ['g', 'g']).buffer.cursor).toBe(0)
  })
  it('G moves to the last line', () => {
    expect(run('a\nb\nc', 0, ['G']).buffer.cursor).toBe(4)
  })
  it('g + non-g cancels the prefix', () => {
    const r = run('abc', 0, ['g', 'z'])
    expect(r.state.pendingG).toBe(false)
    expect(r.buffer.value).toBe('abc')
  })
})

describe('WORD motions (E/B) and inclusive change', () => {
  it('E jumps to end of WORD (whitespace-delimited)', () => {
    expect(resolveMotion('E', 'a.b cd', 0)).toBe(2) // 'a.b' end
  })
  it('B jumps to start of previous WORD', () => {
    expect(resolveMotion('B', 'a.b cd', 4)).toBe(0)
  })
  it('cW changes to end of WORD', () => {
    expect(run('a.b cd', 0, ['c', 'W']).buffer.value).toBe(' cd')
  })
})

describe('edge cases', () => {
  it('empty buffer: motions and x are no-ops', () => {
    expect(run('', 0, ['w']).buffer).toEqual({ value: '', cursor: 0 })
    expect(run('', 0, ['x']).buffer.value).toBe('')
  })
  it('x never splits a surrogate pair (emoji)', () => {
    const r = run('😀b', 0, ['x'])
    expect(r.buffer.value).toBe('b') // whole emoji removed, no lone surrogate
    expect(r.buffer.value.length).toBe(1)
  })
  it('l over an emoji advances a whole code point', () => {
    // '😀b' — from 0, l lands on 'b' (offset 2), not mid-surrogate (offset 1)
    expect(run('😀b', 0, ['l']).buffer.cursor).toBe(2)
  })
  it('combining mark stays with its base letter (word class)', () => {
    // 'á' as base + combining acute: still one word, w moves past both to ' '/end
    const s = 'áb c'
    expect(resolveMotion('w', s, 0)).toBe(4) // past 'áb' to 'c'
  })
})

import { describe, expect, it } from 'vitest'

import { hasAnsi, parseAnsi, stripAnsi, type AnsiLine } from './ansi.ts'

function textOf(line: AnsiLine): string {
  return line.map(span => span.text).join('')
}

describe('parseAnsi', () => {
  it('passes plain text through untouched', () => {
    expect(parseAnsi('hello\nworld')).toEqual([[{ text: 'hello' }], [{ text: 'world' }]])
  })

  it('colors a span and resets', () => {
    const [line] = parseAnsi('\x1b[32mPASS\x1b[0m tests')

    expect(line).toEqual([{ fg: 2, text: 'PASS' }, { text: ' tests' }])
  })

  it('treats an empty SGR as a reset', () => {
    const [line] = parseAnsi('\x1b[31mred\x1b[mplain')

    expect(line).toEqual([{ fg: 1, text: 'red' }, { text: 'plain' }])
  })

  it('combines bold with color and clears bold on 22', () => {
    const [line] = parseAnsi('\x1b[1;31mfail\x1b[22m still red')

    expect(line).toEqual([
      { bold: true, fg: 1, text: 'fail' },
      { fg: 1, text: ' still red' },
    ])
  })

  it('maps bright colors to the high palette', () => {
    const [line] = parseAnsi('\x1b[90mdim gray\x1b[0m')

    expect(line).toEqual([{ fg: 8, text: 'dim gray' }])
  })

  it('carries style across lines until reset', () => {
    const lines = parseAnsi('\x1b[33mone\ntwo\x1b[0m\nthree')

    expect(lines).toEqual([
      [{ fg: 3, text: 'one' }],
      [{ fg: 3, text: 'two' }],
      [{ text: 'three' }],
    ])
  })

  it('resolves 256-color and truecolor forms to CSS', () => {
    const [line] = parseAnsi('\x1b[38;5;196mred\x1b[0m \x1b[38;2;10;20;30mrgb\x1b[0m')

    expect(line?.[0]).toEqual({ fg: 'rgb(255, 0, 0)', text: 'red' })
    expect(line?.[2]).toEqual({ fg: 'rgb(10, 20, 30)', text: 'rgb' })
  })

  it('keeps low 256-color indexes on the palette', () => {
    const [line] = parseAnsi('\x1b[38;5;4mblue\x1b[0m')

    expect(line).toEqual([{ fg: 4, text: 'blue' }])
  })

  it('applies background colors', () => {
    const [line] = parseAnsi('\x1b[41mon red\x1b[49m off')

    expect(line).toEqual([{ bg: 1, text: 'on red' }, { text: ' off' }])
  })

  it('strips cursor movement and OSC sequences', () => {
    expect(textOf(parseAnsi('\x1b[2Kcleared \x1b]0;title\x07line')[0] ?? [])).toBe('cleared line')
  })

  it('ignores unknown SGR codes without dropping text', () => {
    const [line] = parseAnsi('\x1b[5;7mtext\x1b[0m')

    expect(line).toEqual([{ text: 'text' }])
  })

  it('emulates carriage-return overwrite for progress bars', () => {
    const lines = parseAnsi('progress 10%\rprogress 50%\rdone!\nnext')

    expect(lines.map(textOf)).toEqual(['done!', 'next'])
  })

  it('does not treat CRLF as an overwrite', () => {
    expect(parseAnsi('one\r\ntwo').map(textOf)).toEqual(['one', 'two'])
  })

  it('drops a trailing newline like a terminal does', () => {
    expect(parseAnsi('one\n').map(textOf)).toEqual(['one'])
  })

  it('keeps interior blank lines', () => {
    expect(parseAnsi('one\n\ntwo').map(textOf)).toEqual(['one', '', 'two'])
  })

  it('survives a malformed extended color without smearing', () => {
    const [line] = parseAnsi('\x1b[38;5m?\x1b[0m ok')

    expect(textOf(line ?? [])).toBe('? ok')
  })
})

describe('stripAnsi', () => {
  it('yields the visible text alone', () => {
    expect(stripAnsi('\x1b[32mPASS\x1b[0m 12 tests\n\x1b[31mFAIL\x1b[0m 1 test')).toBe(
      'PASS 12 tests\nFAIL 1 test',
    )
  })
})

describe('hasAnsi', () => {
  it('detects escapes and carriage returns', () => {
    expect(hasAnsi('plain')).toBe(false)
    expect(hasAnsi('a\x1b[32mb')).toBe(true)
    expect(hasAnsi('a\rb')).toBe(true)
  })
})

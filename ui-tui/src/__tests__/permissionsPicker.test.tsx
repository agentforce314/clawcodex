import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

import { PermissionsPicker } from '../components/permissionsPicker.js'
import { PERMISSION_LEVELS } from '../lib/permissionLevels.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

/** Mount the picker with a writable stdin so keys can actually be driven —
 *  the digit/arrow/Enter/Esc branches ARE this component's behavior, and a
 *  render-only test leaves all of them uncovered. */
function mount(current: string) {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let out = ''

  stdout.on('data', (c: Buffer) => {
    out += c.toString()
  })
  Object.assign(stdout, { columns: 100, rows: 40 })
  Object.assign(stdin, { isTTY: true, ref: () => {}, setRawMode: () => {}, unref: () => {} })

  const onClose = vi.fn()
  const onSelect = vi.fn()

  const app = renderSync(
    React.createElement(PermissionsPicker, { current, onClose, onSelect, t: DEFAULT_THEME }),
    {
      exitOnCtrlC: false,
      patchConsole: false,
      stderr: stderr as NodeJS.WriteStream,
      stdin: stdin as NodeJS.ReadStream,
      stdout: stdout as NodeJS.WriteStream
    }
  )

  return {
    onClose,
    onSelect,
    output: () => stripAnsi(out),
    async press(seq: string, settle = 20) {
      stdin.write(seq)
      await delay(settle)
    },
    unmount: () => app.unmount()
  }
}

const render = (current: string): string => {
  const p = mount(current)

  p.unmount()

  return p.output()
}

const ARROW_UP = '[A'
const ARROW_DOWN = '[B'
const ENTER = '\r'
const ESC = ''

describe('PermissionsPicker', () => {
  it('lists all three levels, numbered, with their descriptions', () => {
    const out = render('bypassPermissions')

    PERMISSION_LEVELS.forEach((level, i) => {
      expect(out).toContain(`${i + 1}. ${level.label}`)
      // The description is the whole point — a row that only says "Full Access"
      // does not tell the user what they are about to permit.
      expect(out.replace(/\s+/g, ' ')).toContain(level.description.split('.')[0]!)
    })
  })

  it('marks the live mode as current', () => {
    const out = render('bypassPermissions').replace(/\s+/g, ' ')

    expect(out).toContain('Full Access · current')
    expect(out).not.toContain('Ask for approval · current')
  })

  it('marks nothing current for a mode with no level row', () => {
    // plan / dontAsk are real modes the picker deliberately does not list.
    // Showing one of the three as "current" there would be a lie.
    const out = render('plan')

    expect(out).not.toContain('· current')
  })

  it('states the keys that work', () => {
    expect(render('default')).toContain('1-3')
  })
})

describe('PermissionsPicker — key handling', () => {
  it('selects directly by number', async () => {
    for (const [i, level] of PERMISSION_LEVELS.entries()) {
      const p = mount('bypassPermissions')

      await p.press(String(i + 1))
      expect(p.onSelect).toHaveBeenCalledWith(level.key)
      p.unmount()
    }
  })

  it('ignores a digit with no row', async () => {
    const p = mount('bypassPermissions')

    await p.press('4')
    await p.press('0')
    expect(p.onSelect).not.toHaveBeenCalled()
    p.unmount()
  })

  it('Enter applies the highlighted row, which starts on the current level', async () => {
    const p = mount('default')

    await p.press(ENTER)
    expect(p.onSelect).toHaveBeenCalledWith('ask')
    p.unmount()
  })

  it('arrows move the highlight and clamp at both ends', async () => {
    const p = mount('default') // starts on row 1

    await p.press(ARROW_UP) // already at the top — must not wrap or go negative
    await p.press(ENTER)
    expect(p.onSelect).toHaveBeenLastCalledWith('ask')

    await p.press(ARROW_DOWN)
    await p.press(ARROW_DOWN)
    await p.press(ARROW_DOWN) // already at the bottom — must clamp
    await p.press(ENTER)
    expect(p.onSelect).toHaveBeenLastCalledWith('full')
    p.unmount()
  })

  it('starts on row 1 for a mode with no row, instead of a negative index', async () => {
    const p = mount('plan')

    await p.press(ENTER)
    expect(p.onSelect).toHaveBeenCalledWith('ask')
    p.unmount()
  })

  it('Esc cancels without selecting', async () => {
    const p = mount('bypassPermissions')

    // A lone ESC is the prefix of every escape SEQUENCE, so the input parser
    // holds it until it can rule one out — give it longer than a plain key.
    await p.press(ESC, 200)
    expect(p.onClose).toHaveBeenCalled()
    expect(p.onSelect).not.toHaveBeenCalled()
    p.unmount()
  })
})

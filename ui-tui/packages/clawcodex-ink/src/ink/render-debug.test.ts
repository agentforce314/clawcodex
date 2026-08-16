import { mkdtempSync, readFileSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'

import { afterEach, describe, expect, it } from 'vitest'

import { logDroppedRow, logSkippedWideCell, renderDebugEnabled } from './render-debug.js'

const SAMPLE = {
  nextLine: 'after',
  physCursorRow: 12,
  prevLine: 'before',
  screenHeight: 40,
  viewportHeight: 20,
  viewportY: 7,
  y: 3
}

let dir: string | undefined

const useTempLog = (): string => {
  dir = mkdtempSync(join(tmpdir(), 'render-debug-'))
  const file = join(dir, 'trace.log')
  process.env.CLAWCODEX_RENDER_DEBUG_FILE = file

  return file
}

afterEach(() => {
  delete process.env.CLAWCODEX_RENDER_DEBUG
  delete process.env.CLAWCODEX_RENDER_DEBUG_FILE

  if (dir) {
    rmSync(dir, { force: true, recursive: true })
    dir = undefined
  }
})

describe('render-debug', () => {
  it('writes nothing until the flag is set', () => {
    const file = useTempLog()

    expect(renderDebugEnabled()).toBe(false)
    logDroppedRow(SAMPLE)

    expect(() => readFileSync(file)).toThrow()
  })

  it('records the row and the two numbers that explain why it was dropped', () => {
    const file = useTempLog()
    process.env.CLAWCODEX_RENDER_DEBUG = '1'

    expect(renderDebugEnabled()).toBe(true)
    logDroppedRow(SAMPLE)

    const entry = JSON.parse(readFileSync(file, 'utf8').trim())
    expect(entry).toMatchObject({
      event: 'dropped-scrolled-off-row',
      nextLine: 'after',
      // The suspects: the renderer believed row 3 sat above the visible band.
      physCursorRow: 12,
      prevLine: 'before',
      viewportY: 7,
      y: 3
    })
  })

  // The other silent-drop site: a wide char refused at the viewport edge. The
  // model still calls that cell painted, so it never repaints — the tracer has
  // to name this one too or it points at the wrong suspect.
  it('records a wide char refused at the viewport edge', () => {
    const file = useTempLog()
    process.env.CLAWCODEX_RENDER_DEBUG = '1'

    logSkippedWideCell({ char: '世', viewportWidth: 80, x: 79, y: 4 })

    const entry = JSON.parse(readFileSync(file, 'utf8').trim())
    expect(entry).toMatchObject({ char: '世', event: 'skipped-wide-cell-at-edge', viewportWidth: 80, x: 79, y: 4 })
  })

  it('treats 0 and false as off, so an inherited env var cannot switch it on', () => {
    useTempLog()

    for (const v of ['0', 'false', '']) {
      process.env.CLAWCODEX_RENDER_DEBUG = v
      expect(renderDebugEnabled(), `value ${JSON.stringify(v)}`).toBe(false)
    }
  })

  // A read-only cwd or a full disk is not a rendering problem — diagnostics
  // must never be the thing that takes the UI down.
  it('swallows a write failure instead of throwing into the render loop', () => {
    process.env.CLAWCODEX_RENDER_DEBUG = '1'
    process.env.CLAWCODEX_RENDER_DEBUG_FILE = join(tmpdir(), 'no-such-dir-here', 'nested', 'trace.log')

    expect(() => logDroppedRow(SAMPLE)).not.toThrow()
  })
})

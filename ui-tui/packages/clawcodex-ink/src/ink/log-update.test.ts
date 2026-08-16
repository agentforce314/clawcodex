import { describe, expect, it } from 'vitest'

import type { Frame } from './frame.js'
import { LogUpdate } from './log-update.js'
import {
  CellWidth,
  CharPool,
  createScreen,
  HyperlinkPool,
  isEmptyCellAt,
  type Screen,
  setCellAt,
  StylePool
} from './screen.js'

/**
 * Contract tests for LogUpdate.render() — the diff-to-ANSI path that owns
 * whether the terminal picks up each React commit correctly.
 *
 * These tests pin down a few load-bearing invariants so that any fix for
 * the "scattered letters after rapid resize" artifact in xterm.js hosts
 * can be grounded against them.
 */

const stylePool = new StylePool()
const charPool = new CharPool()
const hyperlinkPool = new HyperlinkPool()

const mkScreen = (w: number, h: number) => createScreen(w, h, stylePool, charPool, hyperlinkPool)

const paint = (screen: Screen, y: number, text: string) => {
  for (let x = 0; x < text.length; x++) {
    setCellAt(screen, x, y, {
      char: text[x]!,
      styleId: stylePool.none,
      width: CellWidth.Narrow,
      hyperlink: undefined
    })
  }
}

const mkFrame = (screen: Screen, viewportW: number, viewportH: number, cursorY = 0): Frame => ({
  screen,
  viewport: { width: viewportW, height: viewportH },
  cursor: { x: 0, y: cursorY, visible: true }
})

const stdoutOnly = (diff: ReturnType<LogUpdate['render']>) =>
  diff
    .filter(p => p.type === 'stdout')
    .map(p => (p as { type: 'stdout'; content: string }).content)
    .join('')

const ESC = '\u001b'
const hasDecstbm = (text: string) => new RegExp(`${ESC}\\[\\d+;\\d+r`).test(text)

describe('LogUpdate.render diff contract', () => {
  it('emits only changed cells when most rows match', () => {
    const w = 20
    const h = 4
    const prev = mkScreen(w, h)
    paint(prev, 0, 'HELLO')
    paint(prev, 1, 'WORLD')
    paint(prev, 2, 'STAYSHERE')

    const next = mkScreen(w, h)
    paint(next, 0, 'HELLO')
    paint(next, 1, 'CHANGE')
    paint(next, 2, 'STAYSHERE')
    next.damage = { x: 0, y: 0, width: w, height: h }

    const log = new LogUpdate({ isTTY: true, stylePool })
    const diff = log.render(mkFrame(prev, w, h), mkFrame(next, w, h), true, false)

    const written = stdoutOnly(diff)
    expect(written).toContain('CHANGE')
    expect(written).not.toContain('HELLO')
    expect(written).not.toContain('STAYSHERE')
  })

  // The row-tail erase anchors one column past the last non-empty cell, so
  // "non-empty" has to mean every cell the terminal still shows something in.
  // If a wide char's spacer ever counted as empty the anchor would land ON it
  // and EL(0) would slice the character in half.
  it('a wide char and its spacer both count as painted, so the tail anchor clears them', () => {
    const screen = mkScreen(10, 1)
    setCellAt(screen, 0, 0, { char: '世', styleId: stylePool.none, width: CellWidth.Wide, hyperlink: undefined })
    setCellAt(screen, 1, 0, { char: '', styleId: stylePool.none, width: CellWidth.SpacerTail, hyperlink: undefined })

    expect(isEmptyCellAt(screen, 0, 0), 'wide char cell').toBe(false)
    expect(isEmptyCellAt(screen, 1, 0), 'its spacer tail').toBe(false)
    expect(isEmptyCellAt(screen, 2, 0), 'the column after it').toBe(true)
  })

  // Same anchor, opposite risk: EL honours the current background (BCE), so a
  // space carrying a background style is a VISIBLE block. It must not be
  // treated as blank, or the erase would eat a coloured region.
  it('a styled space counts as painted, so the tail erase cannot eat it', () => {
    const screen = mkScreen(10, 1)
    const styled = stylePool.intern([{ type: 'ansi', code: '[41m', endCode: '[49m' }])

    setCellAt(screen, 0, 0, { char: ' ', styleId: styled, width: CellWidth.Narrow, hyperlink: undefined })

    expect(isEmptyCellAt(screen, 0, 0), 'space with a background style').toBe(false)
  })

  it('width change emits a clearTerminal patch before repainting', () => {
    const prevW = 20
    const nextW = 15
    const h = 3

    const prev = mkScreen(prevW, h)
    paint(prev, 0, 'thiswaswiderrow')

    const next = mkScreen(nextW, h)
    paint(next, 0, 'shorterrownow')
    next.damage = { x: 0, y: 0, width: nextW, height: h }

    const log = new LogUpdate({ isTTY: true, stylePool })
    const diff = log.render(mkFrame(prev, prevW, h), mkFrame(next, nextW, h), true, false)

    expect(diff.some(p => p.type === 'clearTerminal')).toBe(true)
    expect(stdoutOnly(diff)).toContain('shorterrownow')
  })

  it('height growth emits a clearTerminal patch before repainting', () => {
    const w = 20
    const prevH = 3
    const nextH = 6

    const prev = mkScreen(w, prevH)
    paint(prev, 0, 'old rows')

    const next = mkScreen(w, nextH)
    paint(next, 0, 'new rows')
    next.damage = { x: 0, y: 0, width: w, height: nextH }

    const log = new LogUpdate({ isTTY: true, stylePool })
    const diff = log.render(mkFrame(prev, w, prevH), mkFrame(next, w, nextH), true, false)

    expect(diff.some(p => p.type === 'clearTerminal')).toBe(true)
    expect(stdoutOnly(diff)).toContain('newrows')
  })

  it('drift repro: identical prev/next emits no heal, even when the physical terminal is stale', () => {
    // Load-bearing theory for the rapid-resize scattered-letter bug: if the
    // physical terminal has stale cells that prev.screen doesn't know about
    // (e.g. resize-induced reflow wrote past ink's tracked range), the
    // renderer has no signal to heal them. LogUpdate.render only sees
    // prev/next — no view of the physical terminal — so when prev==next,
    // it emits nothing and any orphaned glyphs survive.
    //
    // The fix path is upstream of this diff: either (a) defensively
    // full-repaint on xterm.js frames where prevFrameContaminated is set,
    // or (b) close the drift window so prev.screen cannot diverge.
    const w = 20
    const h = 3

    const prev = mkScreen(w, h)
    paint(prev, 0, 'same')

    const next = mkScreen(w, h)
    paint(next, 0, 'same')
    next.damage = { x: 0, y: 0, width: w, height: h }

    const log = new LogUpdate({ isTTY: true, stylePool })
    const diff = log.render(mkFrame(prev, w, h), mkFrame(next, w, h), true, false)

    expect(stdoutOnly(diff)).toBe('')
    expect(diff.some(p => p.type === 'clearTerminal')).toBe(false)
  })

  it('ignores main-screen scrollback-only changes instead of resetting repeatedly', () => {
    const w = 20
    const viewportH = 5
    const h = 8

    const prev = mkScreen(w, h)
    paint(prev, 0, 'timer 1s')
    paint(prev, 6, 'visible prompt')

    const next = mkScreen(w, h)
    paint(next, 0, 'timer 2s')
    paint(next, 6, 'visible prompt')
    next.damage = { x: 0, y: 0, width: w, height: h }

    const log = new LogUpdate({ isTTY: true, stylePool })
    const diff = log.render(mkFrame(prev, w, viewportH, h), mkFrame(next, w, viewportH, h), false, false)

    expect(diff.some(p => p.type === 'clearTerminal')).toBe(false)
    expect(stdoutOnly(diff)).not.toContain('timer2s')
  })

  it('keeps alt-screen full reset for unreachable scrollback row changes', () => {
    const w = 20
    const viewportH = 5
    const h = 8

    const prev = mkScreen(w, h)
    paint(prev, 0, 'timer 1s')
    paint(prev, 6, 'visible prompt')

    const next = mkScreen(w, h)
    paint(next, 0, 'timer 2s')
    paint(next, 6, 'visible prompt')
    next.damage = { x: 0, y: 0, width: w, height: h }

    const log = new LogUpdate({ isTTY: true, stylePool })
    const diff = log.render(mkFrame(prev, w, viewportH, h), mkFrame(next, w, viewportH, h), true, false)

    expect(diff.some(p => p.type === 'clearTerminal')).toBe(true)
    expect(stdoutOnly(diff)).toContain('timer2s')
  })

  it('keeps DECSTBM fast-path when scroll region stays above bottom row', () => {
    const w = 12
    const h = 6
    const prev = mkScreen(w, h)
    const next = mkScreen(w, h)

    paint(prev, 1, 'row one')
    paint(next, 1, 'row one')

    const prevFrame = mkFrame(prev, w, h)

    const nextFrame: Frame = {
      ...mkFrame(next, w, h),
      scrollHint: { top: 1, bottom: 4, delta: 1 }
    }

    const log = new LogUpdate({ isTTY: true, stylePool })
    const diff = log.render(prevFrame, nextFrame, true, true)

    expect(hasDecstbm(stdoutOnly(diff))).toBe(true)
  })

  it('skips DECSTBM when scroll region touches the bottom row', () => {
    const w = 12
    const h = 6
    const prev = mkScreen(w, h)
    const next = mkScreen(w, h)

    paint(prev, 1, 'row one')
    paint(next, 1, 'row one')

    const prevFrame = mkFrame(prev, w, h)

    const nextFrame: Frame = {
      ...mkFrame(next, w, h),
      scrollHint: { top: 1, bottom: 5, delta: 1 }
    }

    const log = new LogUpdate({ isTTY: true, stylePool })
    const diff = log.render(prevFrame, nextFrame, true, true)

    expect(hasDecstbm(stdoutOnly(diff))).toBe(false)
  })
})

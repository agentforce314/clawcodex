import { stringWidth } from '@clawcodex/ink'
import { describe, expect, it } from 'vitest'

import {
  borderTitleParts,
  fitsHorizontal,
  optimalLeftWidth,
  rightColumnWidth,
  truncatePath,
  welcomeMessage
} from '../components/branding.js'

// The header box allocates width the way the reference banner does
// (typescript/src/utils/logoV2Utils.ts): the left column is sized to its own
// content, the divider and padding are booked explicitly, and the remainder
// goes to the feed column.
//
// Regression this file pins: the old code sized the left column as
// `min(mascotWidth + 4, cols * 0.4)`, which never looked at the cwd or model
// lines. At 140 columns that pinned it to 20, truncating the cwd to
// `/Users/ericlee2/wor…` while ~65 columns sat empty on the right.

const HERO_W = 16

describe('optimalLeftWidth', () => {
  it('sizes to the widest line, not to the mascot', () => {
    const cwd = 'Path /Users/ericlee2/workspace/clawcodex'

    // Widest line + 4 for padding — and well clear of the mascot, which is the
    // whole point: the old code returned mascotWidth + 4 = 20 here.
    expect(optimalLeftWidth([cwd, 'Model opus'], HERO_W)).toBe(cwd.length + 4)
    expect(optimalLeftWidth([cwd, 'Model opus'], HERO_W)).toBeGreaterThan(HERO_W + 4)
  })

  // HERO_W must exceed MIN_LEFT_CONTENT (20) or this is tautological: at 16 the
  // floor dominates and the assertion passes even if heroWidth is ignored.
  it('never drops below the mascot, so a wide custom hero still fits', () => {
    const wideHero = 30

    expect(optimalLeftWidth(['x'], wideHero)).toBe(wideHero + 4)
  })

  it('caps so one deep path cannot starve the feed column', () => {
    const deep = `Path   /${'nested/'.repeat(40)}leaf`

    expect(optimalLeftWidth([deep], HERO_W)).toBe(50)
  })
})

describe('rightColumnWidth', () => {
  it('hands the remainder to the feed column', () => {
    // 138 terminal cols - BORDER_PADDING(4) - DIVIDER_MARGIN(2) - DIVIDER_WIDTH(1)
    // - left(46) = 85.
    expect(rightColumnWidth(138, 46)).toBe(85)
  })

  it('grows as the terminal grows', () => {
    expect(rightColumnWidth(198, 46)).toBeGreaterThan(rightColumnWidth(138, 46))
  })
})

describe('fitsHorizontal', () => {
  it('splits when both columns fit', () => {
    expect(fitsHorizontal(140, 46)).toBe(true)
  })

  it('stacks below the coarse threshold', () => {
    expect(fitsHorizontal(60, 20)).toBe(false)
  })

  // The regression that motivated the fit check: at 72 columns a deep cwd wants
  // a 45-wide left column, so the split overflowed and flexbox shrank BOTH
  // children -- wrapping the collapse toggles into unreadable two-word columns.
  it('stacks when the terminal clears the threshold but the columns do not fit', () => {
    expect(fitsHorizontal(72, 45)).toBe(false)
  })

  it('is monotonic in terminal width for a fixed left column', () => {
    const widths = [70, 80, 90, 100, 120, 160]
    const results = widths.map(c => fitsHorizontal(c, 45))

    expect(results).toEqual([...results].sort((a, b) => Number(a) - Number(b)))
  })

  it('leaves the feed column at least its minimum whenever it splits', () => {
    for (let cols = 20; cols <= 220; cols++) {
      for (const leftW of [20, 32, 45, 50]) {
        if (fitsHorizontal(cols, leftW)) {
          expect(rightColumnWidth(cols, leftW)).toBeGreaterThanOrEqual(36)
        }
      }
    }
  })

  // Guards the overflow that mangles the toggles: when the layout does split,
  // the pieces must fit the interior (terminal - border - paddingX).
  it('never lets the split columns overflow the box interior', () => {
    for (let cols = 20; cols <= 220; cols++) {
      for (const leftW of [20, 32, 45, 50]) {
        if (fitsHorizontal(cols, leftW)) {
          // DIVIDER_MARGIN(2) + DIVIDER_WIDTH(1); the render test in
          // brandingRenderWidth.test.ts is what pins this against the real JSX.
          const gutter = 3

          expect(leftW + gutter + rightColumnWidth(cols, leftW)).toBeLessThanOrEqual(cols - 4)
        }
      }
    }
  })
})

describe('truncatePath', () => {
  it('leaves a path that already fits alone', () => {
    expect(truncatePath('/a/b/c', 40)).toBe('/a/b/c')
  })

  // The point of middle-truncation: end-truncation would hide `components`,
  // the one segment that tells you where you are.
  it('keeps the trailing segments, dropping from the front', () => {
    const out = truncatePath('/Users/me/workspace/clawcodex/ui-tui/src/components', 30)

    expect(out.startsWith('…/')).toBe(true)
    expect(out.endsWith('components')).toBe(true)
    expect(stringWidth(out)).toBeLessThanOrEqual(30)
  })

  it('clips the final segment when not even it fits', () => {
    const out = truncatePath('/Users/me/averyveryverylongdirectoryname', 12)

    expect(stringWidth(out)).toBeLessThanOrEqual(12)
    expect(out.startsWith('…')).toBe(true)
  })

  it('respects the budget across a sweep of widths', () => {
    const path = '/Users/ericlee2/workspace/clawcodex/ui-tui/src/components/deeply/nested'

    for (let w = 6; w <= 80; w++) {
      expect(stringWidth(truncatePath(path, w))).toBeLessThanOrEqual(w)
    }
  })

  // These measure COLUMNS, not code units. A code-unit clip (`.slice` by
  // `.length`) overshoots on wide glyphs and returns a string wider than the
  // budget; ink then truncates it again at the far end, eliding both ends and
  // destroying the trailing segment this function exists to preserve.
  it('respects the budget in columns for wide glyphs', () => {
    const cjk = `/Users/me/${'目'.repeat(30)}/项目目录`

    for (let w = 4; w <= 60; w++) {
      expect(stringWidth(truncatePath(cjk, w))).toBeLessThanOrEqual(w)
    }
  })

  it('respects the budget in columns when the final segment must be clipped', () => {
    // One segment, all wide glyphs — forces the clip branch specifically.
    const wide = `/Users/me/${'目'.repeat(40)}`

    for (const w of [5, 12, 20, 33, 41]) {
      expect(stringWidth(truncatePath(wide, w))).toBeLessThanOrEqual(w)
    }
  })

  it('never splits a surrogate pair', () => {
    for (let w = 3; w <= 30; w++) {
      const out = truncatePath(`/Users/me/${'🎉'.repeat(20)}`, w)

      expect(out).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/)
      expect(out).not.toMatch(/(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/)
      expect(stringWidth(out)).toBeLessThanOrEqual(w)
    }
  })

  // Grapheme clusters, not code points. A keycap is digit + VS16 + U+20E3:
  // summing per code point gives 1 column, but the rendered cluster is 2, so a
  // code-point walk undercounts and overflows the budget.
  it('respects the budget for keycap sequences', () => {
    for (let w = 2; w <= 30; w++) {
      expect(stringWidth(truncatePath(`/Users/me/${'1️⃣'.repeat(12)}`, w))).toBeLessThanOrEqual(w)
    }
  })

  // Regional-indicator flags are pairs; splitting them yields reversed letters.
  it('keeps regional-indicator flags intact', () => {
    const out = truncatePath(`/Users/me/${'🇸🇪'.repeat(10)}`, 20)

    expect(stringWidth(out)).toBeLessThanOrEqual(20)
    // An odd number of regional indicators means a pair was cut in half.
    expect((out.match(/[\u{1F1E6}-\u{1F1FF}]/gu) ?? []).length % 2).toBe(0)
  })

  it('keeps combining marks attached to their base', () => {
    for (let w = 3; w <= 24; w++) {
      const out = truncatePath(`/Users/me/${'é'.repeat(15)}`, w) // e + U+0301

      expect(stringWidth(out)).toBeLessThanOrEqual(w)
      expect(out).not.toMatch(/^[̀-ͯ]/)
    }
  })

  it('returns nothing for a non-positive budget', () => {
    expect(truncatePath('/a/b/c', 0)).toBe('')
    expect(truncatePath('/a/b/c', -5)).toBe('')
  })

  it('handles degenerate paths', () => {
    expect(truncatePath('', 10)).toBe('')
    expect(stringWidth(truncatePath('noslashes-but-really-quite-long', 10))).toBeLessThanOrEqual(10)
    expect(stringWidth(truncatePath('/Users/me/trailing/', 8))).toBeLessThanOrEqual(8)
  })
})

describe('borderTitleParts', () => {
  // Guards the collapse at `cols - 2`, where ink drops the corners and returns
  // the raw title — leaving the box with no top edge at all.
  it('keeps name and version when there is room', () => {
    expect(borderTitleParts(100, 'clawcodex', '1.0.0')).toEqual({ name: 'clawcodex', version: '1.0.0' })
  })

  it('drops the version before dropping the name', () => {
    expect(borderTitleParts(20, 'clawcodex', '1.0.0')).toEqual({ name: 'clawcodex' })
  })

  it('gives up entirely rather than collapse the border', () => {
    expect(borderTitleParts(12, 'clawcodex', '1.0.0')).toBeNull()
    expect(borderTitleParts(30, 'Acme Engineering Assistant', '1.2.1')).toBeNull()
  })

  it('never returns a title that reaches ink\'s collapse threshold', () => {
    for (let cols = 8; cols <= 200; cols++) {
      for (const name of ['clawcodex', 'Acme Engineering Assistant', '目目目目目目目目']) {
        const parts = borderTitleParts(cols, name, '1.2.1')

        if (parts) {
          const rendered = ` ${parts.name}${parts.version ? ` v${parts.version}` : ''} `

          expect(stringWidth(rendered)).toBeLessThan(cols - 2)
        }
      }
    }
  })
})

describe('welcomeMessage', () => {
  it('greets a known user by name', () => {
    expect(welcomeMessage('ericlee2', 'clawcodex')).toBe('Welcome back, ericlee2')
  })

  it('falls back when there is no username', () => {
    expect(welcomeMessage(null, 'clawcodex')).toBe('Welcome to clawcodex')
    expect(welcomeMessage('', 'clawcodex')).toBe('Welcome to clawcodex')
  })

  // A pathological name would otherwise blow out the left column's sizing.
  it('falls back rather than let an absurd name size the column', () => {
    expect(welcomeMessage('x'.repeat(64), 'clawcodex')).toBe('Welcome to clawcodex')
  })
})

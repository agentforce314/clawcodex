import { describe, expect, it } from 'vitest'

import {
  insertPlaceholder,
  isAttached,
  liveAttachments,
  placeholderFor,
  removePlaceholder,
  type Attachment,
} from './attachments.ts'

const shot = (id: number): Attachment => ({ id, name: `shot-${String(id)}.png`, url: `blob:${String(id)}` })

describe('placeholderFor', () => {
  it('matches the chip the backend looks for', () => {
    // Anything else and the image is silently dropped at submit.
    expect(placeholderFor(1)).toBe('[Image #1]')
    expect(placeholderFor(12)).toBe('[Image #12]')
  })
})

describe('isAttached', () => {
  it('follows the draft, not a separate list', () => {
    expect(isAttached('look at [Image #1] please', 1)).toBe(true)
    expect(isAttached('look at this please', 1)).toBe(false)
  })

  it('does not confuse #1 with #12', () => {
    expect(isAttached('see [Image #12]', 1)).toBe(false)
  })
})

describe('liveAttachments', () => {
  it('keeps only what the draft still claims', () => {
    // Deleting the chip is the un-attach gesture; the strip has to agree.
    const kept = liveAttachments('a [Image #2] b', [shot(1), shot(2)])

    expect(kept.map(item => item.id)).toEqual([2])
  })

  it('orders by where the chips appear, not by id', () => {
    const kept = liveAttachments('[Image #3] then [Image #1]', [shot(1), shot(3)])

    expect(kept.map(item => item.id)).toEqual([3, 1])
  })

  it('is empty for a draft that mentions none of them', () => {
    expect(liveAttachments('plain text', [shot(1)])).toEqual([])
  })
})

describe('insertPlaceholder', () => {
  it('inserts at the caret and reports where it lands', () => {
    const result = insertPlaceholder('', 0, 1)

    expect(result.text).toBe('[Image #1] ')
    expect(result.caret).toBe(result.text.length)
  })

  it('spaces the chip off the preceding word', () => {
    // "see this[Image #1]" reads badly and is harder to delete cleanly —
    // and deleting it is the un-attach gesture.
    expect(insertPlaceholder('see this', 8, 1).text).toBe('see this [Image #1] ')
  })

  it('does not double the space when there already is one', () => {
    expect(insertPlaceholder('see this ', 9, 1).text).toBe('see this [Image #1] ')
  })

  it('inserts mid-draft without disturbing the rest', () => {
    expect(insertPlaceholder('before after', 6, 2).text).toBe('before [Image #2] after')
  })

  it('clamps a caret outside the draft', () => {
    expect(insertPlaceholder('abc', 99, 1).text).toBe('abc [Image #1] ')
    expect(insertPlaceholder('abc', -4, 1).text).toBe('[Image #1] abc')
  })
})

describe('removePlaceholder', () => {
  it('drops the chip', () => {
    expect(removePlaceholder('[Image #1] hello', 1)).toBe('hello')
  })

  it('collapses the space the chip leaves behind', () => {
    expect(removePlaceholder('a [Image #1] b', 1)).toBe('a b')
  })

  it('leaves a draft that never had the chip alone', () => {
    expect(removePlaceholder('a b', 1)).toBe('a b')
  })

  it('removes only the chip asked for', () => {
    expect(removePlaceholder('[Image #1] [Image #2]', 1)).toBe('[Image #2]')
  })
})

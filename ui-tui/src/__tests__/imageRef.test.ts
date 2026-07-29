import { describe, expect, it } from 'vitest'

import { formatImageRef, IMAGE_REF_RE, parseImageRefs } from '../protocol/imageRef.js'

describe('formatImageRef', () => {
  it('renders the chip the composer shows', () => {
    expect(formatImageRef(1)).toBe('[Image #1]')
    expect(formatImageRef(12)).toBe('[Image #12]')
  })
})

describe('parseImageRefs', () => {
  it('finds every chip in order', () => {
    expect(parseImageRefs('a [Image #1] b [Image #12] c')).toEqual([1, 12])
  })

  it('ignores id 0 — ids start at 1', () => {
    expect(parseImageRefs('[Image #0]')).toEqual([])
  })

  it('does not match near-misses', () => {
    expect(parseImageRefs('[Image] [Image #] [Pasted text #1] [image #1]')).toEqual([])
  })

  it('returns nothing for an ordinary prompt', () => {
    expect(parseImageRefs('what is this about?')).toEqual([])
  })

  it('is stateless across calls despite the /g flag', () => {
    // A module-level /g regex carries lastIndex; matchAll resets it, but pin it
    // so a refactor to .exec() cannot introduce an every-other-call bug.
    const text = 'x [Image #3] y'
    expect(parseImageRefs(text)).toEqual([3])
    expect(parseImageRefs(text)).toEqual([3])
    expect(IMAGE_REF_RE.lastIndex).toBe(0)
  })
})

/** The backend mirrors this pattern (`_IMAGE_REF_RE` in agent_server.py) and
 *  drops any pending image whose chip is absent from the submitted text. These
 *  cases are the contract both sides have to agree on. */
describe('chip round-trip against the backend contract', () => {
  it('a formatted chip is parseable back to its id', () => {
    for (const id of [1, 2, 9, 10, 99, 1000]) {
      expect(parseImageRefs(`prompt ${formatImageRef(id)} more`)).toEqual([id])
    }
  })

  it('survives being embedded on its own line, which is how it is inserted', () => {
    expect(parseImageRefs(`what is this?\n${formatImageRef(2)}`)).toEqual([2])
  })
})

describe('appendImageChip — composer placement', () => {
  it('puts the chip on its own line under existing text', async () => {
    const { appendImageChip } = await import('../app/useComposerState.js')

    expect(appendImageChip('what this image is about?', 2).value).toBe(
      'what this image is about?\n[Image #2] '
    )
  })

  it('is the only content in an empty composer', async () => {
    const { appendImageChip } = await import('../app/useComposerState.js')

    expect(appendImageChip('', 1).value).toBe('[Image #1] ')
  })

  it('does not double the newline', async () => {
    const { appendImageChip } = await import('../app/useComposerState.js')

    expect(appendImageChip('line\n', 3).value).toBe('line\n[Image #3] ')
  })

  it('leaves the cursor after the chip', async () => {
    const { appendImageChip } = await import('../app/useComposerState.js')
    const out = appendImageChip('hi', 4)

    expect(out.cursor).toBe(out.value.length)
  })

  it('keeps a dropped-path remainder after the chip', async () => {
    const { appendImageChip } = await import('../app/useComposerState.js')

    // No trailing space before a newline — it would be invisible whitespace.
    expect(appendImageChip('look:', 5, 'and this too').value).toBe(
      'look:\n[Image #5]\nand this too'
    )
  })

  it('stacks multiple attachments, each parseable', async () => {
    const { appendImageChip } = await import('../app/useComposerState.js')
    const one = appendImageChip('compare these', 1).value
    const two = appendImageChip(one, 2).value

    expect(parseImageRefs(two)).toEqual([1, 2])
  })
})

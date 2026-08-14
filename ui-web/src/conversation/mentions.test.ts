import { describe, expect, it } from 'vitest'

import { applyMention, mentionAt } from './mentions.ts'

/** `mentionAt` with the caret marked by `|` in the fixture, for readability. */
function at(fixture: string) {
  const caret = fixture.indexOf('|')
  const text = fixture.replace('|', '')

  return mentionAt(text, caret)
}

describe('mentionAt', () => {
  it('finds a mention being typed at the caret', () => {
    expect(at('@src/cli|')).toEqual({ end: 8, query: 'src/cli', start: 0 })
  })

  it('finds one mid-sentence, which is where they usually are', () => {
    expect(at('look at @src/cl|')).toMatchObject({ query: 'src/cl', start: 8 })
  })

  it('treats a bare @ as an open mention with an empty query', () => {
    // Typing @ should open the menu on the whole file list, not wait for a
    // character first.
    expect(at('@|')).toEqual({ end: 1, query: '', start: 0 })
  })

  it('ignores an @ that does not open a word', () => {
    // An email address and an array index are text, not triggers.
    expect(at('me@example|')).toBeNull()
    expect(at('arr@[0|')).toBeNull()
  })

  it('ends the mention at whitespace', () => {
    // Once the path is settled the menu should close, not keep matching.
    expect(at('@src/cli.py |')).toBeNull()
    expect(at('@src/cli.py and then|')).toBeNull()
  })

  it('tracks the caret rather than the end of the draft', () => {
    // Editing a mention earlier in a finished sentence still works: caret 8
    // is the end of the first mention, and caret 9 is past its space, which
    // is no longer a mention at all.
    expect(mentionAt('@src/cli and @ui-web/src', 8)).toMatchObject({ query: 'src/cli' })
    expect(mentionAt('@src/cli and @ui-web/src', 9)).toBeNull()
  })

  it('picks the mention the caret is in when there are several', () => {
    const text = 'compare @a.ts with @b.ts'

    expect(mentionAt(text, text.length)).toMatchObject({ query: 'b.ts', start: 19 })
  })

  it('finds nothing in a draft with no @ at all', () => {
    expect(at('just some words|')).toBeNull()
    expect(mentionAt('', 0)).toBeNull()
  })

  it('clamps a caret outside the text instead of throwing', () => {
    expect(mentionAt('@src', 99)).toMatchObject({ query: 'src' })
    expect(mentionAt('@src', -5)).toBeNull()
  })
})

describe('applyMention', () => {
  it('replaces the token and reports where the caret lands', () => {
    const token = mentionAt('@src/cl', 7)!
    const result = applyMention('@src/cl', token, 'src/cli.py')

    expect(result.text).toBe('@src/cli.py ')
    expect(result.caret).toBe(result.text.length)
  })

  it('keeps the text on both sides of the token', () => {
    const text = 'look at @src/cl and say why'
    const token = mentionAt(text, 15)!

    expect(applyMention(text, token, 'src/cli.py').text).toBe(
      'look at @src/cli.py  and say why',
    )
  })

  it('leaves the caret after the trailing space, not inside the path', () => {
    // The space closes the mention so the menu shuts and the next keystroke
    // starts a new word.
    const text = 'look at @src/cl and say why'
    const token = mentionAt(text, 15)!
    const { caret, text: next } = applyMention(text, token, 'src/cli.py')

    expect(next.slice(caret - 1, caret)).toBe(' ')
    expect(mentionAt(next, caret)).toBeNull()
  })

  it('completes a bare @ into a full mention', () => {
    const token = mentionAt('@', 1)!

    expect(applyMention('@', token, 'README.md').text).toBe('@README.md ')
  })
})

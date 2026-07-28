import { describe, expect, it } from 'vitest'

import { looksLikeDroppedPath } from '../app/useComposerState.js'

describe('looksLikeDroppedPath', () => {
  it('recognizes macOS screenshot temp paths and file URIs', () => {
    expect(looksLikeDroppedPath('/var/folders/x/T/TemporaryItems/Screenshot\\ 2026-04-21\\ at\\ 1.04.43 PM.png')).toBe(
      true
    )
    expect(
      looksLikeDroppedPath('file:///var/folders/x/T/TemporaryItems/Screenshot%202026-04-21%20at%201.04.43%20PM.png')
    ).toBe(true)
  })

  it('rejects normal multiline or plain text paste', () => {
    expect(looksLikeDroppedPath('hello world')).toBe(false)
    expect(looksLikeDroppedPath('line one\nline two')).toBe(false)
  })

  it('recognizes common image file extensions', () => {
    expect(looksLikeDroppedPath('/Users/me/Desktop/photo.jpg')).toBe(true)
    expect(looksLikeDroppedPath('/Users/me/Desktop/diagram.png')).toBe(true)
    expect(looksLikeDroppedPath('/tmp/capture.webp')).toBe(true)
    expect(looksLikeDroppedPath('/tmp/image.gif')).toBe(true)
  })

  it('recognizes file:// URIs with various extensions', () => {
    expect(looksLikeDroppedPath('file:///home/user/doc.pdf')).toBe(true)
    expect(looksLikeDroppedPath('file:///tmp/screenshot.png')).toBe(true)
  })

  it('recognizes paths with spaces (not backslash-escaped)', () => {
    expect(looksLikeDroppedPath('/var/folders/x/T/TemporaryItems/Screenshot 2026-04-21 at 1.04.43 PM.png')).toBe(true)
  })

  it('rejects empty/whitespace-only input', () => {
    expect(looksLikeDroppedPath('')).toBe(false)
    expect(looksLikeDroppedPath('   ')).toBe(false)
    expect(looksLikeDroppedPath('\n')).toBe(false)
  })

  it('rejects URLs that are not file:// URIs', () => {
    expect(looksLikeDroppedPath('https://example.com/image.png')).toBe(false)
    expect(looksLikeDroppedPath('http://localhost/file.pdf')).toBe(false)
  })

  it('rejects short slash-like strings without path structure', () => {
    // No second '/' or '.' → not a plausible file path
    expect(looksLikeDroppedPath('/help')).toBe(false)
    expect(looksLikeDroppedPath('/model sonnet')).toBe(false)
    expect(looksLikeDroppedPath('/api')).toBe(false)
  })

  it('accepts absolute paths with directory separators or extensions', () => {
    expect(looksLikeDroppedPath('/usr/bin/test')).toBe(true)
    expect(looksLikeDroppedPath('/tmp/file.txt')).toBe(true)
    expect(looksLikeDroppedPath('/etc/hosts')).toBe(true) // has second /
  })
})

// `useSubmission` runs `input.detect_drop` on SUBMITTED prompts, and that RPC
// only became live when its gatewayClient case was added. Every prompt this
// predicate lets through reaches a backend that may resolve it to a real file
// and REWRITE the prompt to `@<abspath>`. These are the strings that must never
// get that far.
describe('looksLikeDroppedPath — submit-path gate', () => {
  it('rejects ordinary prompts that name a real file', () => {
    expect(looksLikeDroppedPath('README.md')).toBe(false)
    expect(looksLikeDroppedPath('package.json')).toBe(false)
    expect(looksLikeDroppedPath('src/gatewayClient.ts')).toBe(false)
    expect(looksLikeDroppedPath('fix the bug in main.py')).toBe(false)
    expect(looksLikeDroppedPath('explain README.md to me')).toBe(false)
  })

  it('rejects questions and prose that merely end in an extension', () => {
    expect(looksLikeDroppedPath('what is wrong with logo.png')).toBe(false)
    expect(looksLikeDroppedPath('why does test.py fail')).toBe(false)
  })

  it('still lets a genuine dragged path through', () => {
    expect(looksLikeDroppedPath('/Users/me/Desktop/shot.png')).toBe(true)
    expect(looksLikeDroppedPath('~/Desktop/shot.png')).toBe(true)
    expect(looksLikeDroppedPath('./shot.png')).toBe(true)
  })
})

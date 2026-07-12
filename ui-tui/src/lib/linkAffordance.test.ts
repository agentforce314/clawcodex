import { supportsHyperlinks } from '@clawcodex/ink'
import { beforeEach, describe, expect, it } from 'vitest'

import { LINK_TIP_TEXT, linkOpenHotkey, linkTipFor, resetLinkTipForTests } from './linkAffordance.js'
import { isMac } from './platform.js'

const APPLE = { TERM_PROGRAM: 'Apple_Terminal' } as NodeJS.ProcessEnv
const XTERM = { TERM_PROGRAM: undefined } as NodeJS.ProcessEnv

describe('linkOpenHotkey', () => {
  it('advertises the platform click gesture when the terminal supports OSC 8', () => {
    const mod = isMac ? 'Cmd' : 'Ctrl'

    expect(linkOpenHotkey({ TERM_PROGRAM: 'iTerm.app' } as NodeJS.ProcessEnv, true)).toEqual([
      `${mod}+click link`,
      `open link in browser (${mod}+hover underlines it)`
    ])
  })

  it('advertises Cmd+double-click on the URL in Apple Terminal (no OSC 8)', () => {
    expect(linkOpenHotkey(APPLE, false)).toEqual(['Cmd+double-click URL', 'open link in browser'])
  })

  it('stays quiet in unknown terminals without OSC 8 support', () => {
    expect(linkOpenHotkey(XTERM, false)).toBeNull()
  })

  it('advertises a plain click in fullscreen mode, where the in-process opener handles every terminal', () => {
    const row = ['click link', 'open link in browser (highlights on hover)']

    expect(linkOpenHotkey(APPLE, false, false)).toEqual(row)
    expect(linkOpenHotkey(XTERM, false, false)).toEqual(row)
  })
})

describe('supportsHyperlinks terminal detection', () => {
  it('recognizes VS Code (xterm.js has handled OSC 8 Cmd+click since 1.72)', () => {
    expect(supportsHyperlinks({ env: { TERM_PROGRAM: 'vscode' }, stdoutSupported: false })).toBe(true)
  })

  it('does not recognize Apple Terminal (no OSC 8 support as of macOS 26)', () => {
    expect(supportsHyperlinks({ env: { TERM_PROGRAM: 'Apple_Terminal' }, stdoutSupported: false })).toBe(false)
  })
})

describe('linkTipFor', () => {
  beforeEach(() => {
    resetLinkTipForTests()
  })

  it('fires once for the first assistant text containing a URL in Apple Terminal', () => {
    expect(linkTipFor(['see https://example.com/docs'], APPLE, false)).toBe(LINK_TIP_TEXT)

    // Second linky message: already shown this session.
    expect(linkTipFor(['more at http://example.org'], APPLE, false)).toBeNull()
  })

  it('does not consume the once-flag on messages without URLs', () => {
    expect(linkTipFor(['no links here'], APPLE, false)).toBeNull()
    expect(linkTipFor(['now with [docs](https://example.com)'], APPLE, false)).toBe(LINK_TIP_TEXT)
  })

  it('stays quiet when the terminal supports OSC 8 hyperlinks', () => {
    expect(linkTipFor(['https://example.com'], APPLE, true)).toBeNull()
  })

  it('stays quiet in fullscreen mode, where a plain click opens links in-process', () => {
    expect(linkTipFor(['https://example.com'], APPLE, false, false)).toBeNull()

    // And it must not have consumed the once-flag.
    expect(linkTipFor(['https://example.com'], APPLE, false, true)).toBe(LINK_TIP_TEXT)
  })

  it('stays quiet outside Apple Terminal — the gesture is Apple Terminal-specific', () => {
    expect(linkTipFor(['https://example.com'], XTERM, false)).toBeNull()
    expect(linkTipFor(['https://example.com'], { TERM_PROGRAM: 'WarpTerminal' } as NodeJS.ProcessEnv, false)).toBeNull()
  })

  it('scans every text in the batch', () => {
    expect(linkTipFor(['first part', 'second: https://example.com/x'], APPLE, false)).toBe(LINK_TIP_TEXT)
  })
})

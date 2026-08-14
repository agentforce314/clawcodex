import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { resolveBackend, resolveToken } from './boot.ts'

// jsdom's own document origin: the client always targets the page it is
// served from, so the test asserts against wherever that happens to be.
const ORIGIN = 'http://localhost:3000'
const WS_ORIGIN = 'ws://localhost:3000'

function setLocation(search: string): void {
  window.history.replaceState(null, '', `/${search}`)
}

beforeEach(() => {
  window.localStorage.clear()
  delete window.__CLAWCODEX_SESSION_TOKEN__
  setLocation('')
})

afterEach(() => {
  window.localStorage.clear()
})

describe('resolveToken', () => {
  it('reads the token the backend inlined into the page', () => {
    window.__CLAWCODEX_SESSION_TOKEN__ = 'inlined'

    expect(resolveToken()).toBe('inlined')
  })

  it('prefers an explicit ?token= over the inlined one', () => {
    window.__CLAWCODEX_SESSION_TOKEN__ = 'inlined'
    setLocation('?token=explicit')

    expect(resolveToken()).toBe('explicit')
  })

  it('strips the token from the address bar after adopting it', () => {
    setLocation('?token=secret&keep=1')
    resolveToken()

    expect(window.location.search).not.toContain('secret')
    expect(window.location.search).toContain('keep=1')
  })

  it('remembers an adopted token across reloads', () => {
    setLocation('?token=remembered')
    resolveToken()
    setLocation('')

    expect(resolveToken()).toBe('remembered')
  })

  it('returns empty when there is nothing to adopt', () => {
    expect(resolveToken()).toBe('')
  })
})

describe('resolveBackend', () => {
  it('targets the page origin, with the token on the socket URL', () => {
    window.__CLAWCODEX_SESSION_TOKEN__ = 'tok en'

    const target = resolveBackend()

    expect(target.apiBase).toBe(`${ORIGIN}/api`)
    // Encoded, not interpolated: a token is opaque and may contain anything.
    expect(target.wsUrl).toBe(`${WS_ORIGIN}/api/ws?token=tok%20en`)
  })

  it('omits the query entirely when there is no token', () => {
    expect(resolveBackend().wsUrl).toBe(`${WS_ORIGIN}/api/ws`)
  })
})

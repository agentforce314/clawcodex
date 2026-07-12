import { describe, expect, it } from 'vitest'

import { normalizeExternalUrl } from '../lib/externalLink.js'

describe('external link helpers', () => {
  it('normalizes scheme-less links', () => {
    expect(normalizeExternalUrl(' expedia.com/things-to-do/puerto-rico-el-yunque ')).toBe(
      'https://expedia.com/things-to-do/puerto-rico-el-yunque'
    )
  })

  it('leaves URLs with schemes and non-domain text untouched', () => {
    expect(normalizeExternalUrl('https://example.com/docs')).toBe('https://example.com/docs')
    expect(normalizeExternalUrl('mailto:user@example.com')).toBe('mailto:user@example.com')
    expect(normalizeExternalUrl('not a url')).toBe('not a url')
  })
})

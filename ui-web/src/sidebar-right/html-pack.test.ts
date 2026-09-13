import { describe, expect, it, vi } from 'vitest'

import {
  createHtmlDocument,
  decodeText,
  encodeBase64,
  isRelativeReference,
  MAX_ASSETS,
  packHtml,
  referencePath,
} from './html-pack.ts'
import { decodeBase64 } from './text-store.ts'

const bytes = (text: string) => new TextEncoder().encode(text)

describe('references', () => {
  it('accepts a relative path and refuses URLs, absolute paths, fragments and queries', () => {
    expect(isRelativeReference('css/app.css')).toBe(true)
    expect(isRelativeReference('../shared.js')).toBe(true)
    expect(isRelativeReference('https://cdn.example.com/x.js')).toBe(false)
    expect(isRelativeReference('/absolute.css')).toBe(false)
    expect(isRelativeReference('#top')).toBe(false)
    expect(isRelativeReference('?v=2')).toBe(false)
    expect(isRelativeReference('')).toBe(false)
  })

  it('strips a query and fragment and decodes percent-encoding, and throws for anything else', () => {
    expect(referencePath('css/app.css?v=3#x')).toBe('css/app.css')
    expect(referencePath('my%20styles.css')).toBe('my styles.css')
    expect(() => referencePath('/etc/passwd')).toThrow()
    expect(() => referencePath('https://x/y.css')).toThrow()
    expect(() => referencePath('a\\b.css')).toThrow()
  })
})

describe('packHtml', () => {
  const html = `<!doctype html><html><head>
    <link rel="stylesheet" href="css/app.css">
    <link rel="icon" href="favicon.ico">
    <link rel="stylesheet" href="https://cdn.example.com/x.css">
    <script src="app.js?v=1"></script>
    <script type="module" src="mod.js"></script>
    <script src="app.js?v=1"></script>
    </head><body>hi</body></html>`

  it('reads the relative stylesheets and classic scripts once each, in document order', async () => {
    const readRelative = vi.fn(async (reference: string) => bytes(`content of ${reference}`))

    const bundle = await packHtml(bytes(html), readRelative, new AbortController().signal)

    expect(readRelative.mock.calls.map(call => call[0])).toEqual(['css/app.css', 'app.js?v=1'])
    expect(bundle.assets).toEqual([
      { kind: 'stylesheet', reference: 'css/app.css', text: 'content of css/app.css' },
      { kind: 'script', reference: 'app.js?v=1', text: 'content of app.js?v=1' },
    ])
    expect(bundle.html).toBe(html)
  })

  it('packs nothing under a base element, leaving resolution to the browser', async () => {
    const readRelative = vi.fn()

    const bundle = await packHtml(
      bytes('<base href="https://example.com/"><link rel="stylesheet" href="a.css">'),
      readRelative,
      new AbortController().signal,
    )

    expect(readRelative).not.toHaveBeenCalled()
    expect(bundle.assets).toEqual([])
  })

  it('refuses a package past the asset count and rejects on a read that fails', async () => {
    const many = Array.from({ length: MAX_ASSETS + 1 }, (_, i) => `<script src="s${i}.js"></script>`).join('')

    await expect(packHtml(bytes(many), async () => bytes('x'), new AbortController().signal)).rejects.toThrow(
      /asset count/,
    )
    await expect(
      packHtml(
        bytes('<link rel="stylesheet" href="gone.css">'),
        async () => {
          throw new Error('That file is gone.')
        },
        new AbortController().signal,
      ),
    ).rejects.toThrow('That file is gone.')
  })

  it('stops when aborted', async () => {
    const controller = new AbortController()

    controller.abort()

    await expect(packHtml(bytes('<p>hi</p>'), async () => bytes(''), controller.signal)).rejects.toThrow()
  })
})

describe('createHtmlDocument', () => {
  it('carries the bundle as a payload the frame decodes back to the same text', () => {
    const bundle = { assets: [{ kind: 'script' as const, reference: 'a.js', text: 'alert("héllo")' }], html: '<p>héllo</p>' }
    const outer = createHtmlDocument(bundle)
    const payload = /text\("([^"]+)"\)/.exec(outer)?.[1]

    expect(outer.startsWith('<!doctype html>')).toBe(true)
    expect(outer).toContain('sandbox' === 'sandbox' ? 'document.write' : '')
    expect(payload).toBeDefined()
    expect(JSON.parse(decodeText(decodeBase64(payload ?? '')))).toEqual(bundle)
  })

  it('round-trips text through base64', () => {
    expect(decodeText(decodeBase64(encodeBase64('héllo, 世界')))).toBe('héllo, 世界')
  })
})

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { linkifyText } from './linkify.tsx'

afterEach(cleanup)

function renderLine(text: string) {
  return render(<div>{linkifyText(text)}</div>)
}

describe('linkifyText', () => {
  it('leaves plain text untouched', () => {
    const { container } = renderLine('nothing to see here')

    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).toBe('nothing to see here')
  })

  it('turns a bare URL into a safe external link', () => {
    const { container } = renderLine('see https://react.dev/blog for details')
    const anchor = container.querySelector('a')

    expect(anchor?.getAttribute('href')).toBe('https://react.dev/blog')
    expect(anchor?.getAttribute('rel')).toBe('noopener noreferrer')
    expect(anchor?.getAttribute('target')).toBe('_blank')
    expect(container.textContent).toBe('see https://react.dev/blog for details')
  })

  it('keeps trailing punctuation out of the address', () => {
    const { container } = renderLine('read https://example.com/docs.')

    expect(container.querySelector('a')?.getAttribute('href')).toBe('https://example.com/docs')
    expect(container.textContent).toBe('read https://example.com/docs.')
  })

  it('links every URL on the line', () => {
    const { container } = renderLine('https://a.io and https://b.io')

    expect(container.querySelectorAll('a')).toHaveLength(2)
  })

  it('ignores non-http protocols', () => {
    const { container } = renderLine('ftp://files.example.com and javascript:alert(1)')

    expect(container.querySelector('a')).toBeNull()
  })
})

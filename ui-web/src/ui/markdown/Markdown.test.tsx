import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Markdown } from './Markdown.tsx'

afterEach(cleanup)

describe('Markdown math', () => {
  it('tokenizes display math whose TeX contains markdown escapes', async () => {
    // `\,` lexes as a markdown escape; the old post-hoc regex never saw the
    // delimiters as one span and printed the raw dollars.
    const { container, findByText } = render(
      <Markdown text={'$$\\int_{-\\infty}^{\\infty} e^{-x^2}\\,dx = \\sqrt{\\pi}$$'} />,
    )

    expect(container.textContent).not.toContain('$$')
    // KaTeX loads async; the raw TeX shows until it lands.
    await findByText(/dx/, undefined, { timeout: 5000 })
  })

  it('renders single-dollar inline math', () => {
    const { container } = render(<Markdown text="Euler: $e^{i\pi} + 1 = 0$ holds." />)

    expect(container.textContent).not.toContain('$')
    expect(container.textContent).toContain('e^{i\\pi} + 1 = 0')
  })

  it('renders \\(...\\) and \\[...\\] delimiters', () => {
    const { container } = render(<Markdown text={'Inline \\(a+b\\) and block:\n\n\\[a-b\\]'} />)

    expect(container.textContent).toContain('a+b')
    expect(container.textContent).toContain('a-b')
    expect(container.textContent).not.toContain('\\(')
    expect(container.textContent).not.toContain('\\[')
  })

  it('leaves prices alone', () => {
    const { container } = render(<Markdown text="It costs $5 and then $10 more." />)

    expect(container.textContent).toContain('It costs $5 and then $10 more.')
  })

  it('leaves an unclosed dollar span literal', () => {
    const { container } = render(<Markdown text="A lone $ sign stays." />)

    expect(container.textContent).toContain('A lone $ sign stays.')
  })
})

describe('Markdown task lists', () => {
  it('draws the checkbox without the literal marker', () => {
    const { container } = render(<Markdown text={'- [ ] open item\n- [x] done item'} />)

    const boxes = container.querySelectorAll('input[type="checkbox"]')

    expect(boxes).toHaveLength(2)
    expect((boxes[1] as HTMLInputElement).checked).toBe(true)
    expect(container.textContent).not.toContain('[ ]')
    expect(container.textContent).not.toContain('[x]')
    expect(container.textContent).toContain('open item')
  })
})

describe('Markdown link safety', () => {
  it('keeps https links, opening them in a new tab', () => {
    const { container } = render(<Markdown text="[docs](https://example.com/docs)" />)
    const anchor = container.querySelector('a')

    expect(anchor?.getAttribute('href')).toBe('https://example.com/docs')
    expect(anchor?.getAttribute('target')).toBe('_blank')
    expect(anchor?.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('drops the anchor from a javascript: link but keeps its text', () => {
    const { container } = render(<Markdown text="[click me](javascript:alert(1))" />)

    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).toContain('click me')
  })

  it('drops the anchor from a data: link', () => {
    const { container } = render(<Markdown text="[x](data:text/html,<script>1</script>)" />)

    expect(container.querySelector('a')).toBeNull()
  })

  it('turns a backticked URL into a link around the code chip', () => {
    const { container } = render(<Markdown text="See `https://example.com/api`." />)
    const anchor = container.querySelector('a')

    expect(anchor?.getAttribute('href')).toBe('https://example.com/api')
    expect(anchor?.querySelector('code')?.textContent).toBe('https://example.com/api')
  })

  it('keeps a plain code chip for non-URL code', () => {
    const { container } = render(<Markdown text="Call `fetch()` now." />)

    expect(container.querySelector('a')).toBeNull()
    expect(container.querySelector('code')?.textContent).toBe('fetch()')
  })
})

describe('Markdown image safety', () => {
  it('renders absolute http(s) images lazily and without a referrer', () => {
    const { container } = render(<Markdown text="![alt text](https://example.com/x.png)" />)
    const image = container.querySelector('img')

    expect(image?.getAttribute('src')).toBe('https://example.com/x.png')
    expect(image?.getAttribute('loading')).toBe('lazy')
    expect(image?.getAttribute('referrerpolicy')).toBe('no-referrer')
  })

  it('shows alt text instead of a relative or data: image', () => {
    const { container } = render(<Markdown text="![diagram](../secret.png)" />)

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('diagram')
  })
})

describe('Markdown raw HTML', () => {
  it('shows markup as source, never mounted', () => {
    const { container } = render(<Markdown text={'<div onclick="alert(1)">boom</div>'} />)

    expect(container.querySelector('div[onclick]')).toBeNull()
    expect(container.textContent).toContain('<div onclick="alert(1)">')
  })
})

describe('Markdown streaming', () => {
  const DOC = [
    '# Title',
    '',
    'First paragraph with **bold** text.',
    '',
    '```python',
    'print("hi")',
    '```',
    '',
    'Second paragraph.',
    '',
    '- item one',
    '- item two',
    '',
    'Closing line.',
  ].join('\n')

  it('renders a growing document identically to the settled render', () => {
    const streamed = render(<Markdown streaming text={DOC.slice(0, 10)} />)

    // Feed the document in uneven chunks, as deltas would.
    for (const cut of [25, 60, 61, 100, 140, DOC.length]) {
      streamed.rerender(<Markdown streaming text={DOC.slice(0, cut)} />)
    }

    streamed.rerender(<Markdown text={DOC} />)

    const settled = render(<Markdown text={DOC} />)

    expect(streamed.container.textContent).toBe(settled.container.textContent)
    expect(streamed.container.querySelectorAll('h1')).toHaveLength(1)
    expect(streamed.container.querySelectorAll('li')).toHaveLength(2)
  })

  it("keeps a frozen block's element identity across deltas", () => {
    const first = render(<Markdown streaming text={DOC} />)
    const heading = first.container.querySelector('h1')

    first.rerender(<Markdown streaming text={DOC + '\n\nMore text.'} />)

    // Same DOM node, not a re-created equal one: the frozen prefix is reused.
    expect(first.container.querySelector('h1')).toBe(heading)
  })

  it('restarts cleanly when the text is rewritten', () => {
    const view = render(<Markdown streaming text="Alpha beta" />)

    view.rerender(<Markdown streaming text="Something else entirely" />)

    expect(view.container.textContent).toContain('Something else entirely')
    expect(view.container.textContent).not.toContain('Alpha')
  })

  it('shows the caret only while streaming', () => {
    const view = render(<Markdown streaming text="Hello" />)

    expect(view.container.querySelector('[aria-hidden]')).not.toBeNull()

    view.rerender(<Markdown text="Hello" />)

    expect(view.container.querySelector('[aria-hidden]')).toBeNull()
  })
})

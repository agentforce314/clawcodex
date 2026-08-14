import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Markdown } from './Markdown.tsx'

afterEach(cleanup)

describe('Markdown', () => {
  it('renders headings, emphasis and links', () => {
    const { container } = render(
      <Markdown text={'# Title\n\nSome **bold** and [a link](https://example.com).'} />,
    )

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Title')
    expect(container.querySelector('strong')?.textContent).toBe('bold')

    const link = screen.getByRole('link', { name: 'a link' })
    expect(link.getAttribute('href')).toBe('https://example.com')
    // An external target without noopener hands the opened page a window
    // handle back to this one.
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    expect(link.getAttribute('target')).toBe('_blank')
  })

  it('renders a fenced block as a code card with its language', () => {
    const { container } = render(<Markdown text={'```ts\nconst a = 1\n```'} />)

    expect(container.textContent).toContain('const a = 1')
    expect(container.textContent).toContain('typescript')
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy()
  })

  it('renders a GFM table inside its own scrollport', () => {
    const { container } = render(<Markdown text={'| a | b |\n| - | - |\n| 1 | 2 |'} />)

    expect(container.querySelectorAll('th')).toHaveLength(2)
    expect(container.querySelectorAll('td')).toHaveLength(2)
    // A wide table must scroll itself rather than widen the transcript column.
    const scroll = container.querySelector('table')?.parentElement
    expect(scroll?.className).toMatch(/tableScroll/)
  })

  it('renders task list items as checkboxes', () => {
    const { container } = render(<Markdown text={'- [x] done\n- [ ] todo'} />)

    const boxes = container.querySelectorAll('input[type="checkbox"]')
    expect(boxes).toHaveLength(2)
    expect((boxes[0] as HTMLInputElement).checked).toBe(true)
    expect((boxes[1] as HTMLInputElement).checked).toBe(false)
  })

  it('shows raw HTML as text instead of mounting it', () => {
    // Model output is untrusted: a transcript is not a place markup executes.
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)">'} />)

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">')
  })

  it.each([
    'It costs $5 and then $10 more.',
    'A range of $5-$10 per seat.',
    'Set $PATH and $HOME before running.',
  ])('does not treat prose like %j as math', text => {
    const { container } = render(<Markdown text={text} />)

    expect(container.querySelector('.katex')).toBeNull()
    expect(container.textContent).toBe(text)
  })

  it('still splits real inline math out of a sentence', () => {
    const { container } = render(<Markdown text={'The area is $x^2$ exactly.'} />)

    // KaTeX loads lazily, so the TeX shows as code until it lands — either
    // way the delimiters are gone and the expression is its own node.
    expect(container.textContent).not.toContain('$')
    expect(container.textContent).toContain('x^2')
  })

  it('paints a caret only while the reply is streaming', () => {
    const { container, rerender } = render(<Markdown streaming text="partial" />)
    expect(container.querySelector('[class*=cursor]')).not.toBeNull()

    rerender(<Markdown text="partial" />)
    expect(container.querySelector('[class*=cursor]')).toBeNull()
  })

  it('renders an unterminated fence rather than failing', () => {
    // Every streaming reply passes through this state on its way to a block.
    const { container } = render(<Markdown streaming text={'```ts\nconst a ='} />)

    expect(container.textContent).toContain('const a =')
  })
})

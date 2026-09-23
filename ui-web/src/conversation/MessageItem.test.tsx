import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { hydrateStoredMessages, type UserNode } from '../state/transcript.ts'
import { UserMessage } from './MessageItem.tsx'

afterEach(cleanup)

const image = {
  name: 'screenshot.png',
  placeholder: '[Image #2]',
  url: 'data:image/png;base64,c2NyZWVuc2hvdA==',
}
const node: UserNode = {
  at: 0, id: 'user-1', images: [image], kind: 'user', text: '[Image #2] what this image is about?',
}

describe('user image messages', () => {
  it('renders the image before the caption without its leading attachment marker', () => {
    const onEdit = vi.fn()
    render(<UserMessage node={node} onEdit={onEdit} />)

    const preview = screen.getByRole('img', { name: 'screenshot.png' })
    const caption = screen.getByText('what this image is about?')
    expect(preview.getAttribute('src')).toBe(image.url)
    expect(preview.compareDocumentPosition(caption) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByText(/\[Image #2\]/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    expect(onEdit).toHaveBeenCalledWith(node.text)
  })

  it('shows an image-only turn without an empty text bubble', () => {
    const { container } = render(<UserMessage node={{ ...node, text: '[Image #2]' }} />)
    expect(screen.getByRole('img')).toBeTruthy()
    expect(container.querySelector('[class*="bubble"]')).toBeNull()
  })

  it('keeps unresolved placeholders and references inside a sentence', () => {
    render(<UserMessage node={{ ...node, text: '[Image #9] compare [Image #2] with the original' }} />)
    expect(screen.getByText('[Image #9] compare [Image #2] with the original')).toBeTruthy()
  })

  it('renders multiple images from a reopened conversation and hides backend metadata', () => {
    const [restored] = hydrateStoredMessages([{
      role: 'user',
      content: [
        { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'b25l' } },
        { type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data: 'dHdv' } },
        { type: 'text', text: '[Image #2] [Image #3] compare these' },
        { type: 'text', text: '[Image: source: /tmp/upload.png]' },
        { type: 'text', text: '[Image: original 2000x1000, displayed at 1000x500. Multiply coordinates by 2.00 to map to original image.]' },
      ],
    }])
    if (restored?.kind !== 'user') throw new Error('missing restored user message')
    const { container } = render(<UserMessage node={restored} />)

    expect(screen.getAllByRole('img').map(element => element.getAttribute('src'))).toEqual([
      'data:image/png;base64,b25l', 'data:image/jpeg;base64,dHdv',
    ])
    expect(screen.getByText('compare these')).toBeTruthy()
    expect(container.textContent).not.toContain('/tmp/upload.png')
    expect(container.textContent).not.toContain('Multiply coordinates')
  })
})

describe('user file messages', () => {
  it('renders a card per attached file before the caption, without the leading chip', () => {
    render(
      <UserMessage
        node={{
          at: 0,
          files: [{ name: 'notes.txt', placeholder: '[File #1]', size: 11 }],
          id: 'user-2',
          kind: 'user',
          text: '[File #1] summarise this',
        }}
      />,
    )

    const card = document.querySelector('[data-file-card]') as HTMLElement
    expect(card.textContent).toContain('notes.txt')
    expect(card.textContent).toContain('TXT · 11 B')
    expect(screen.getByText('summarise this')).toBeTruthy()
    expect(screen.queryByText(/\[File #1\]/)).toBeNull()
    expect(card.compareDocumentPosition(screen.getByText('summarise this')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shows a reopened file turn as a card and hides the contents the agent inlined', () => {
    const [restored] = hydrateStoredMessages([{
      role: 'user',
      content: [
        { type: 'text', text: '[File #1] what is in it' },
        { type: 'text', text: '[File #1: secrets.env] saved at /tmp/a/secrets.env (20 B)\n<system-reminder>\nContents of secrets.env:\n```\nTOKEN=abc\n```\n</system-reminder>' },
      ],
    }])
    if (restored?.kind !== 'user') throw new Error('missing restored user message')
    const { container } = render(<UserMessage node={restored} />)

    expect(container.querySelector('[data-file-card]')?.textContent).toContain('secrets.env')
    expect(screen.getByText('what is in it')).toBeTruthy()
    expect(container.textContent).not.toContain('TOKEN=abc')
    expect(container.textContent).not.toContain('system-reminder')
  })
})

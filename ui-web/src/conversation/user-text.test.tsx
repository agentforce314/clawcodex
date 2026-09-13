import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { projectUserText, resolveMention } from './user-text.tsx'

afterEach(cleanup)

describe('resolveMention', () => {
  it('reads a relative mention against the workspace and leaves an absolute one alone', () => {
    expect(resolveMention('src/app.ts', '/repo')).toBe('/repo/src/app.ts')
    expect(resolveMention('src/app.ts', '/repo/')).toBe('/repo/src/app.ts')
    expect(resolveMention('/etc/hosts', '/repo')).toBe('/etc/hosts')
    expect(resolveMention('src/app.ts', undefined)).toBe('src/app.ts')
  })
})

describe('projectUserText', () => {
  it('returns plain text untouched when nothing is mentioned', () => {
    expect(projectUserText('fix the bug in me@example.com')).toBe('fix the bug in me@example.com')
  })

  it('turns an @path into a chip that opens the resolved file', () => {
    const onOpen = vi.fn()

    render(<p>{projectUserText('look at @src/app.ts and @"my notes.md" please', { onOpen, workspace: '/repo' })}</p>)

    fireEvent.click(screen.getByRole('button', { name: 'app.ts' }))
    fireEvent.click(screen.getByRole('button', { name: 'my notes.md' }))

    expect(onOpen.mock.calls).toEqual([['/repo/src/app.ts'], ['/repo/my notes.md']])
    expect(screen.getByRole('button', { name: 'app.ts' }).getAttribute('title')).toBe('/repo/src/app.ts')
  })

  it('keeps a folder as a chip without a click, and the words around it', () => {
    const { container } = render(<p>{projectUserText('read @docs/ first', { onOpen: vi.fn() })}</p>)

    expect(container.textContent).toBe('read docs first')
    expect(screen.queryByRole('button')).toBeNull()
    expect(container.querySelector('[data-ref-chip="folder"]')).not.toBeNull()
  })
})

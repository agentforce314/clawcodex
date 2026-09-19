import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { ProjectNode } from '../gateway/protocol.ts'
import { $projects, $sessionId, $storedSessionId, $workspace } from '../state/store.ts'
import { Sidebar } from './Sidebar.tsx'

function project(id: string, path = `/${id}`): ProjectNode {
  return {
    id,
    label: id,
    path: `/${id}`,
    repos: [{
      id,
      label: id,
      path: `/${id}`,
      groups: [{
        id: `${id}-lane`,
        label: 'main',
        path,
        sessions: [{ id: `${id}-session`, title: `${id} conversation` }],
      }],
    }],
  }
}

function folder(name: string): HTMLElement {
  return screen.getByRole('button', { name: new RegExp(`^${name}\\s*1$`) })
}

beforeEach(() => {
  $projects.set([project('alpha'), project('beta', '/beta-worktree'), project('gamma')])
  $workspace.set('')
  $sessionId.set(null)
  $storedSessionId.set(null)
})

afterEach(() => {
  cleanup()
  $projects.set([])
  $workspace.set('')
  $sessionId.set(null)
  $storedSessionId.set(null)
})

describe('workspace folder expansion', () => {
  it('opens only the stored session folder, including a linked worktree', () => {
    // A restored conversation has a fresh runtime id, and the launch
    // workspace may differ from the session restored into the window.
    $workspace.set('/alpha')
    $sessionId.set('fresh-runtime')
    $storedSessionId.set('beta-session')

    render(<Sidebar collapsed={false} />)

    expect(folder('alpha').getAttribute('aria-expanded')).toBe('false')
    expect(folder('beta').getAttribute('aria-expanded')).toBe('true')
    expect(folder('gamma').getAttribute('aria-expanded')).toBe('false')
    expect(screen.getByText('beta conversation')).toBeTruthy()
    expect(screen.queryByText('alpha conversation')).toBeNull()
    expect(screen.queryByText('gamma conversation')).toBeNull()
  })

  it('leaves all folders collapsed without an active workspace or session', () => {
    render(<Sidebar collapsed={false} />)

    expect(screen.queryByText(/conversation/)).toBeNull()
    expect(folder('alpha').getAttribute('aria-expanded')).toBe('false')
    expect(folder('beta').getAttribute('aria-expanded')).toBe('false')
    expect(folder('gamma').getAttribute('aria-expanded')).toBe('false')
  })

  it('replaces the launch workspace default when the saved session is restored', () => {
    $workspace.set('/beta-worktree')
    render(<Sidebar collapsed={false} />)

    expect(screen.getByText('beta conversation')).toBeTruthy()
    expect(screen.queryByText('alpha conversation')).toBeNull()

    act(() => {
      $sessionId.set('fresh-runtime')
      $storedSessionId.set('alpha-session')
      $workspace.set('/alpha')
    })

    expect(screen.getByText('alpha conversation')).toBeTruthy()
    expect(screen.queryByText('beta conversation')).toBeNull()
    expect(screen.queryByText('gamma conversation')).toBeNull()
  })

  it('opens the live session folder when the project tree arrives later', () => {
    $projects.set([])
    $sessionId.set('beta-session')
    $storedSessionId.set('beta-session')
    render(<Sidebar collapsed={false} />)

    act(() => {
      $projects.set([project('alpha'), project('beta')])
    })

    expect(screen.getByText('beta conversation')).toBeTruthy()
    expect(screen.queryByText('alpha conversation')).toBeNull()
  })

  it('preserves manual toggles through refreshes and session selection', () => {
    $storedSessionId.set('alpha-session')
    render(<Sidebar collapsed={false} />)

    fireEvent.click(folder('alpha'))
    fireEvent.click(folder('gamma'))

    act(() => {
      $projects.set([project('alpha'), project('beta'), project('gamma')])
      $storedSessionId.set('beta-session')
    })

    expect(screen.queryByText('alpha conversation')).toBeNull()
    expect(screen.getByText('beta conversation')).toBeTruthy()
    expect(screen.getByText('gamma conversation')).toBeTruthy()

    act(() => { $storedSessionId.set('alpha-session') })

    expect(screen.queryByText('alpha conversation')).toBeNull()
    expect(screen.getByText('beta conversation')).toBeTruthy()
    expect(screen.getByText('gamma conversation')).toBeTruthy()
  })

  it('opens search matches temporarily without changing folder toggles', () => {
    $storedSessionId.set('alpha-session')
    render(<Sidebar collapsed={false} />)
    fireEvent.click(folder('gamma'))

    fireEvent.change(screen.getByLabelText('Filter sessions'), {
      target: { value: 'beta conversation' },
    })

    expect(screen.getByText('beta conversation')).toBeTruthy()
    expect(screen.queryByText('alpha conversation')).toBeNull()

    fireEvent.click(screen.getByLabelText('Clear the filter'))

    expect(screen.getByText('alpha conversation')).toBeTruthy()
    expect(screen.queryByText('beta conversation')).toBeNull()
    expect(screen.getByText('gamma conversation')).toBeTruthy()
  })
})

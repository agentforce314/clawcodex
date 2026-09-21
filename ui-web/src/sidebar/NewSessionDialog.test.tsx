import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ProjectNode } from '../gateway/protocol.ts'
import { $newSessionDialog, $projects, $workspace } from '../state/store.ts'

const createSession = vi.fn<(options?: Record<string, unknown>) => Promise<string | null>>()

vi.mock('../state/actions.ts', () => ({
  createSession: (options?: Record<string, unknown>) => createSession(options),
}))

const { NEW_WORKSPACE, NewSessionDialog, knownWorkspaces, openNewSessionDialog } = await import('./NewSessionDialog.tsx')

function project(path: string | null): ProjectNode {
  return { id: path ?? 'home', label: path ?? 'Home', path, repos: [] }
}

beforeEach(() => {
  createSession.mockReset()
  createSession.mockResolvedValue(null)
  $workspace.set('/work/current')
  $projects.set([project('/work/alpha'), project('/work/current'), project(null)])
  $newSessionDialog.set(false)
})

afterEach(() => {
  cleanup()
  $newSessionDialog.set(false)
  $projects.set([])
  $workspace.set('')
})

describe('knownWorkspaces', () => {
  it('lists the current workspace first, then the sidebar folders, once each, never Home', () => {
    expect(knownWorkspaces('/work/current', [project('/work/alpha'), project('/work/current'), project(null)]))
      .toEqual(['/work/current', '/work/alpha'])
    expect(knownWorkspaces('', [project('/a')])).toEqual(['/a'])
  })
})

describe('NewSessionDialog', () => {
  it('is closed until opened, and starts a session in the chosen workspace', async () => {
    render(<NewSessionDialog />)

    expect(screen.queryByRole('dialog')).toBeNull()

    act(() => {
      openNewSessionDialog()
    })

    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('/work/current')
    expect(screen.queryByPlaceholderText('/absolute/path/to/project')).toBeNull()

    fireEvent.change(select, { target: { value: '/work/alpha' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await act(async () => {
      await Promise.resolve()
    })

    expect(createSession).toHaveBeenCalledWith({ cwd: '/work/alpha' })
    expect($newSessionDialog.get()).toBe(false)
  })

  it('creates a new workspace from an absolute path, in a worktree when asked', async () => {
    $newSessionDialog.set(true)
    render(<NewSessionDialog />)

    const select = screen.getByRole('combobox') as HTMLSelectElement
    fireEvent.change(select, { target: { value: NEW_WORKSPACE } })

    const create = screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement
    expect(create.disabled).toBe(true)

    fireEvent.change(screen.getByPlaceholderText('/absolute/path/to/project'), {
      target: { value: '/work/fresh ' },
    })
    fireEvent.click(screen.getByRole('switch'))
    expect(create.disabled).toBe(false)

    fireEvent.click(create)
    await act(async () => {
      await Promise.resolve()
    })

    expect(createSession).toHaveBeenCalledWith({ createDir: true, cwd: '/work/fresh', worktree: true })
    expect($newSessionDialog.get()).toBe(false)
  })

  it('keeps the dialog open with the reason when the backend refuses', async () => {
    createSession.mockResolvedValue('no such directory: /nope')
    $newSessionDialog.set(true)
    render(<NewSessionDialog />)

    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.getByRole('alert').textContent).toBe('no such directory: /nope')
    expect($newSessionDialog.get()).toBe(true)
  })

  it('closes on Cancel and on Escape', () => {
    $newSessionDialog.set(true)
    render(<NewSessionDialog />)

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect($newSessionDialog.get()).toBe(false)

    act(() => {
      openNewSessionDialog()
    })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect($newSessionDialog.get()).toBe(false)
    expect(createSession).not.toHaveBeenCalled()
  })

  it('offers only the new-workspace path when no workspace is known', () => {
    $workspace.set('')
    $projects.set([])
    $newSessionDialog.set(true)
    render(<NewSessionDialog />)

    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe(NEW_WORKSPACE)
    expect(screen.getByPlaceholderText('/absolute/path/to/project')).toBeTruthy()
  })
})

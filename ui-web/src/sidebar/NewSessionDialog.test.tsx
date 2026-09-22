import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ProjectNode } from '../gateway/protocol.ts'
import { $newSessionDialog, $projects, $workspace } from '../state/store.ts'

const createSession = vi.fn<(options?: Record<string, unknown>) => Promise<string | null>>()

vi.mock('../state/actions.ts', () => ({
  createSession: (options?: Record<string, unknown>) => createSession(options),
}))

const { NEW_WORKSPACE, NewSessionDialog, knownWorkspaces, openNewSessionDialog, workspaceRows } =
  await import('./NewSessionDialog.tsx')

function project(path: string | null): ProjectNode {
  return { id: path ?? 'home', label: path ?? 'Home', path, repos: [] }
}

/** The picker's trigger: the one control labelled "Workspace". */
function picker(): HTMLButtonElement {
  return screen.getByRole('button', { name: /^Workspace/ }) as HTMLButtonElement
}

function openPicker(): HTMLElement {
  fireEvent.click(picker())

  return screen.getByRole('menu')
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

  it('offers a repo’s worktree lanes as well as the repo', () => {
    const repo: ProjectNode = {
      id: '/repo',
      label: 'repo',
      path: '/repo',
      repos: [{
        groups: [
          { id: 'main', label: 'main', path: '/repo', sessions: [] },
          { id: 'wt', label: 'feature', path: '/repo/.clawcodex/worktrees/feature', sessions: [] },
        ],
        id: '/repo',
        label: 'repo',
        path: '/repo',
      }],
    }

    expect(knownWorkspaces('', [repo])).toEqual(['/repo', '/repo/.clawcodex/worktrees/feature'])
  })
})

describe('workspaceRows', () => {
  it('names a row by its folder, and adds the path only where two folders share a name', () => {
    const rows = workspaceRows(['/work/alpha', '/other/alpha', '/work/beta'])

    expect(rows.map(row => ('label' in row ? row.label : null))).toEqual(['alpha', 'alpha', 'beta'])
    expect(rows.map(row => ('hint' in row ? row.hint : undefined))).toEqual(['/work/alpha', '/other/alpha', undefined])
  })
})

describe('NewSessionDialog', () => {
  it('is closed until opened, and starts a session in the workspace picked from the list', async () => {
    render(<NewSessionDialog />)

    expect(screen.queryByRole('dialog')).toBeNull()

    act(() => {
      openNewSessionDialog()
    })

    // The trigger names the current workspace; the list is closed.
    expect(picker().textContent).toContain('current')
    expect(picker().textContent).toContain('/work/current')
    expect(screen.queryByRole('menu')).toBeNull()
    expect(screen.queryByPlaceholderText('/absolute/path/to/project')).toBeNull()

    const menu = openPicker()
    const rows = within(menu).getAllByRole('menuitem')
    expect(rows.map(row => row.textContent)).toEqual(['current', 'alpha', 'Add workspace…'])

    fireEvent.click(within(menu).getByRole('menuitem', { name: 'alpha' }))
    expect(screen.queryByRole('menu')).toBeNull()
    expect(picker().textContent).toContain('/work/alpha')

    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await act(async () => {
      await Promise.resolve()
    })

    expect(createSession).toHaveBeenCalledWith({ cwd: '/work/alpha' })
    expect($newSessionDialog.get()).toBe(false)
  })

  it('pins Add workspace… below the scrolling list, after a divider', () => {
    // A long list: the action must not be its last row.
    $projects.set(Array.from({ length: 40 }, (_, index) => project(`/work/project-${String(index)}`)))
    $newSessionDialog.set(true)
    render(<NewSessionDialog />)

    const menu = openPicker()
    const viewport = menu.querySelector('[class*="viewport"]')
    const add = within(menu).getByRole('menuitem', { name: 'Add workspace…' })

    expect(viewport).not.toBeNull()
    expect(viewport?.contains(add)).toBe(false)
    expect(within(viewport as HTMLElement).getAllByRole('menuitem')).toHaveLength(41)
    // The divider sits between the list and the pinned row.
    expect(add.previousElementSibling?.getAttribute('role')).toBe('separator')
  })

  it('creates a new workspace from an absolute path, in a worktree when asked', async () => {
    $newSessionDialog.set(true)
    render(<NewSessionDialog />)

    fireEvent.click(within(openPicker()).getByRole('menuitem', { name: 'Add workspace…' }))
    expect(picker().textContent).toContain('New workspace')

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

  it('marks the picked workspace in the list, and none while adding one', () => {
    $newSessionDialog.set(true)
    render(<NewSessionDialog />)

    let menu = openPicker()
    const checked = (row: HTMLElement) => row.querySelector('svg[class*="check"]') !== null
    expect(checked(within(menu).getByRole('menuitem', { name: 'current' }))).toBe(true)
    expect(checked(within(menu).getByRole('menuitem', { name: 'alpha' }))).toBe(false)

    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Add workspace…' }))
    menu = openPicker()
    expect(within(menu).getAllByRole('menuitem').some(checked)).toBe(false)
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

  it('lets Escape and an outside press close the open list, not the dialog', () => {
    $newSessionDialog.set(true)
    render(<NewSessionDialog />)

    openPicker()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect($newSessionDialog.get()).toBe(true)

    openPicker()
    const scrim = screen.getByRole('dialog').parentElement as HTMLElement
    fireEvent.pointerDown(scrim)
    fireEvent.click(scrim)
    expect(screen.queryByRole('menu')).toBeNull()
    expect($newSessionDialog.get()).toBe(true)

    // With the list closed, the same press on the scrim closes the dialog.
    fireEvent.pointerDown(scrim)
    fireEvent.click(scrim)
    expect($newSessionDialog.get()).toBe(false)
  })

  it('offers only the new-workspace path when no workspace is known', () => {
    $workspace.set('')
    $projects.set([])
    $newSessionDialog.set(true)
    render(<NewSessionDialog />)

    expect(picker().textContent).toContain('New workspace')
    expect(screen.getByPlaceholderText('/absolute/path/to/project')).toBeTruthy()

    const menu = openPicker()
    expect(within(menu).getAllByRole('menuitem').map(row => row.textContent)).toEqual(['Add workspace…'])
    expect(NEW_WORKSPACE).toBe('__new_workspace__')
  })
})

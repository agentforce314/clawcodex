import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GatewayClient } from '../gateway/client.ts'
import { setGatewayClient } from '../state/actions.ts'
import { $detailsWidth, closeDetails } from '../state/layout.ts'
import { $transcript, $workspace } from '../state/store.ts'
import { emptyTranscript } from '../state/transcript.ts'
import { resetTree } from './files-store.ts'
import { SidebarRight } from './SidebarRight.tsx'
import {
  $activeTabId,
  $fullscreen,
  $tabs,
  closeSidebar,
  openFile,
  resetSidebar,
} from './store.ts'
import { resetTextTabs } from './text-store.ts'

const request = vi.fn()

beforeEach(() => {
  request.mockReset()
  // One socket, two vocabularies: the tree lists levels, the preview reads
  // pages, and a blanket reply would hand each the other's shape.
  request.mockImplementation(async (method: string) =>
    method === 'fs.read_file'
      ? {
          absolute_path: '/repo/a.ts',
          bytes: 4,
          eof: true,
          lines: 1,
          offset: 1,
          ok: true,
          text: 'one',
          version: 'v1',
        }
      : { absolute_path: '/repo', entries: [], ok: true, truncated: false },
  )
  setGatewayClient({ request } as unknown as GatewayClient)
  $workspace.set('/repo')
  $transcript.set(emptyTranscript())
  resetSidebar()
  resetTextTabs()
  resetTree()
})

afterEach(() => {
  cleanup()
  setGatewayClient(null)
  $workspace.set('')
  $transcript.set(emptyTranscript())
  resetSidebar()
  resetTextTabs()
  resetTree()
  closeDetails()
})

describe('SidebarRight', () => {
  it('opens on the session tab', () => {
    render(<SidebarRight />)

    expect(screen.getByRole('tab', { name: 'Session' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByText('Files touched')).toBeTruthy()
  })

  it('opens the workspace tree through the start page, which then gives way', async () => {
    render(<SidebarRight />)

    fireEvent.click(screen.getByLabelText('New tab'))

    expect(screen.getByRole('tab', { name: 'Start' }).getAttribute('aria-selected')).toBe('true')
    // One start page at a time: the add control leaves the strip while it is open.
    expect(screen.queryByLabelText('New tab')).toBeNull()

    fireEvent.click(screen.getByText('Workspace files'))

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Files' }).getAttribute('aria-selected')).toBe('true')
    })
    expect(screen.queryByRole('tab', { name: 'Start' })).toBeNull()
  })

  it('draws a chip per open file and switches between them', async () => {
    openFile('/repo/a.ts')
    openFile('/repo/b.ts')

    render(<SidebarRight />)

    expect(screen.getByRole('tab', { name: 'b.ts' }).getAttribute('aria-selected')).toBe('true')

    fireEvent.click(screen.getByRole('tab', { name: 'a.ts' }))

    expect($activeTabId.get()).toBe('text:/repo/a.ts')
  })

  it('closes a file tab from its chip', () => {
    openFile('/repo/a.ts')

    render(<SidebarRight />)
    fireEvent.click(screen.getByLabelText('Close a.ts'))

    expect($tabs.get().map(tab => tab.kind)).toEqual(['session'])
  })

  it('keeps the session tab, which is the column floor', () => {
    render(<SidebarRight />)

    expect(screen.queryByLabelText('Close Session')).toBeNull()
  })

  it('goes full screen and back from the strip', () => {
    const { container } = render(<SidebarRight />)

    fireEvent.click(screen.getByLabelText('Full screen'))

    expect($fullscreen.get()).toBe(true)
    expect(container.querySelector('[data-fullscreen]')).not.toBeNull()

    fireEvent.click(screen.getByLabelText('Leave full screen'))

    expect($fullscreen.get()).toBe(false)
  })

  it('leaves full screen behind when the column closes', () => {
    render(<SidebarRight />)

    fireEvent.click(screen.getByLabelText('Full screen'))
    fireEvent.click(screen.getByLabelText('Close the sidebar'))

    // Otherwise reopening the column would land the reader in a full-screen
    // panel they never asked for.
    expect($fullscreen.get()).toBe(false)
    expect($detailsWidth.get()).toBe(0)
  })

  it('closes the same way from every other control', () => {
    // ⌘I and the conversation header call closeSidebar too, so the rule above
    // holds for them by construction rather than by three copies of it.
    render(<SidebarRight />)

    fireEvent.click(screen.getByLabelText('Full screen'))
    closeSidebar()

    expect($fullscreen.get()).toBe(false)
    expect($detailsWidth.get()).toBe(0)
  })
})

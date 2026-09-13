import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { $detailsWidth, closeDetails } from '../state/layout.ts'
import {
  $activeTabId,
  $fullscreen,
  $navigation,
  $tabs,
  basename,
  closeTab,
  focusTab,
  GUIDE_TAB,
  openFile,
  openGuide,
  openPage,
  resetSidebar,
  SESSION_TAB,
  toggleFullscreen,
} from './store.ts'

beforeEach(() => {
  resetSidebar()
  closeDetails()
})

afterEach(() => {
  resetSidebar()
  closeDetails()
})

describe('basename', () => {
  it('takes the last segment of either separator', () => {
    expect(basename('/repo/src/app.ts')).toBe('app.ts')
    expect(basename('C:\\repo\\src\\app.ts')).toBe('app.ts')
  })

  it('keeps a bare name, and a separator-only path, as itself', () => {
    expect(basename('notes.md')).toBe('notes.md')
    expect(basename('/')).toBe('/')
  })
})

describe('the column', () => {
  it('opens with the session tab and nothing else', () => {
    expect($tabs.get()).toEqual([SESSION_TAB])
    expect($activeTabId.get()).toBe(SESSION_TAB.id)
  })

  it('expands the column when something is put in it', () => {
    // Content the reader cannot see is not opened.
    expect($detailsWidth.get()).toBe(0)

    openFile('/repo/src/app.ts')

    expect($detailsWidth.get()).toBeGreaterThan(0)
  })

  it('names a file tab by its last segment and focuses it', () => {
    openFile('/repo/src/app.ts')

    expect($tabs.get().map(tab => tab.title)).toEqual(['Session', 'app.ts'])
    expect($activeTabId.get()).toBe('text:/repo/src/app.ts')
  })

  it('reveals a file already open rather than opening it twice', () => {
    openFile('/repo/src/app.ts')
    openPage('files')
    openFile('/repo/src/app.ts')

    expect($tabs.get().filter(tab => tab.kind === 'text')).toHaveLength(1)
    expect($activeTabId.get()).toBe('text:/repo/src/app.ts')
  })

  it('counts every open as a navigation, so re-clicking a path jumps again', () => {
    const id = openFile('/repo/src/app.ts', 12)

    expect($navigation.get()[id]).toEqual({ line: 12, revision: 1 })

    openFile('/repo/src/app.ts', 40)

    expect($navigation.get()[id]).toEqual({ line: 40, revision: 2 })
  })

  it('holds one tab per address, so two files with one name are two tabs', () => {
    openFile('/repo/src/app.ts')
    openFile('/repo/test/app.ts')

    expect($tabs.get().filter(tab => tab.title === 'app.ts')).toHaveLength(2)
  })

  it('keeps one page tab per kind', () => {
    openPage('files')
    openPage('files')
    openPage('session')

    expect($tabs.get().map(tab => tab.kind)).toEqual(['session', 'files'])
    expect($activeTabId.get()).toBe(SESSION_TAB.id)
  })

  it('hands focus to the tab on the left when the active one closes', () => {
    openFile('/repo/a.ts')
    const second = openFile('/repo/b.ts')

    closeTab(second)

    expect($activeTabId.get()).toBe('text:/repo/a.ts')
  })

  it('leaves focus alone when a background tab closes', () => {
    const first = openFile('/repo/a.ts')

    openFile('/repo/b.ts')
    closeTab(first)

    expect($activeTabId.get()).toBe('text:/repo/b.ts')
  })

  it('forgets a closed tab, so reopening it is a fresh occurrence', () => {
    const id = openFile('/repo/a.ts', 3)

    closeTab(id)

    expect($navigation.get()[id]).toBeUndefined()

    openFile('/repo/a.ts')

    expect($navigation.get()[id]?.revision).toBe(1)
  })

  it('ignores a close for a tab that is not open', () => {
    closeTab('text:/repo/never.ts')

    expect($tabs.get()).toEqual([SESSION_TAB])
  })

  it('focuses only tabs that exist', () => {
    focusTab('text:/repo/nope.ts')

    expect($activeTabId.get()).toBe(SESSION_TAB.id)
  })

  it('goes full screen and back', () => {
    toggleFullscreen()
    expect($fullscreen.get()).toBe(true)

    toggleFullscreen()
    expect($fullscreen.get()).toBe(false)
  })

  it('returns to the opening shape on reset', () => {
    openFile('/repo/a.ts')
    openPage('files')
    toggleFullscreen()

    resetSidebar()

    expect($tabs.get()).toEqual([SESSION_TAB])
    expect($activeTabId.get()).toBe(SESSION_TAB.id)
    expect($navigation.get()).toEqual({})
    expect($fullscreen.get()).toBe(false)
  })
})

describe('the start page', () => {
  it('opens once and focuses on a second ask', () => {
    openGuide()
    openGuide()

    expect($tabs.get().filter(tab => tab.kind === 'guide')).toHaveLength(1)
    expect($activeTabId.get()).toBe(GUIDE_TAB.id)
  })

  it('gives way to the page it opens, in its own slot', () => {
    openGuide()
    openPage('files', GUIDE_TAB.id)

    expect($tabs.get().map(tab => tab.kind)).toEqual(['session', 'files'])
    expect($activeTabId.get()).toBe('files:')
  })

  it('closes when the page it opens is already in the strip', () => {
    openPage('files')
    openGuide()
    openPage('files', GUIDE_TAB.id)

    expect($tabs.get().map(tab => tab.kind)).toEqual(['session', 'files'])
    expect($activeTabId.get()).toBe('files:')
  })

  it('opens the session facts from the start page and closes itself', () => {
    openGuide()
    openPage('session', GUIDE_TAB.id)

    expect($tabs.get()).toEqual([SESSION_TAB])
    expect($activeTabId.get()).toBe(SESSION_TAB.id)
  })
})

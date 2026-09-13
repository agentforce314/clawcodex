/**
 * The right column's tabs: what is open beside the conversation, and which of
 * them is showing.
 *
 * The column used to be one fixed panel of session facts. It is now a small
 * docking surface: the session facts are one tab, the workspace tree is
 * another, and every file the conversation names opens as its own. That is the
 * shape the reference client moved to, minus its split/float engine — a plugin
 * runtime's worth of machinery for a column this app never splits.
 *
 * **A tab's identity is `kind + address`.** Opening the same file twice
 * therefore focuses the tab it is already in (and re-answers its line) rather
 * than stacking duplicates, which is the whole reason the address is the id.
 *
 * State only; the asynchronous halves live beside their bodies
 * (`text-store.ts`, `files-store.ts`) and the gateway calls they make are the
 * ones declared in `state/actions.ts`.
 */

import { atom, map } from 'nanostores'

import { closeDetails, openDetails } from '../state/layout.ts'
import { resetTree } from './files-store.ts'
import { forgetTextTab, resetTextTabs } from './text-store.ts'

export type SidebarTabKind = 'files' | 'guide' | 'session' | 'text'

export interface SidebarTab {
  /** The file's absolute path for `text`; empty for the page tabs. */
  address: string
  id: string
  kind: SidebarTabKind
  /** Captured when the tab opens and never rewritten. */
  title: string
}

/** Where a navigation asked the body to go, and how many times it has asked. */
export interface SidebarNavigation {
  /** 1-based line to jump to, when the caller named one. */
  line?: number
  /**
   * Bumped on every open of this tab, whether or not `line` changed — a body
   * acts on "navigated again" alone, so re-clicking the same path re-jumps.
   */
  revision: number
}

export const SESSION_TAB: SidebarTab = {
  address: '',
  id: 'session:',
  kind: 'session',
  title: 'Session',
}

const FILES_TAB: SidebarTab = { address: '', id: 'files:', kind: 'files', title: 'Files' }

/**
 * The start page: a door to each page the column can show. It is what the
 * strip's `+` opens, and it gives way to whatever it opens.
 */
export const GUIDE_TAB: SidebarTab = { address: '', id: 'guide:', kind: 'guide', title: 'Start' }

export const $tabs = atom<SidebarTab[]>([SESSION_TAB])
export const $activeTabId = atom<string>(SESSION_TAB.id)
/** The column covering the whole frame, for reading something long. */
export const $fullscreen = atom<boolean>(false)
export const $navigation = map<Record<string, SidebarNavigation>>({})

/** The last path segment — what a tab chip can show of a path. */
export function basename(path: string): string {
  const segments = path.split(/[/\\]/).filter(Boolean)

  return segments.at(-1) ?? path
}

function focus(id: string): void {
  $activeTabId.set(id)
  // Content the reader cannot see is not opened: a collapsed column expands in
  // the same gesture that put something in it.
  openDetails()
}

/**
 * Open a page tab (the session facts, the file tree), or focus the open one.
 *
 * With `replace`, the page takes the named tab's slot — how the guide gives
 * way to the page it opened, as the reference's does — and the named tab
 * closes even when the page was already open elsewhere in the strip.
 */
export function openPage(kind: 'files' | 'session', replace?: string): void {
  const wanted = kind === 'files' ? FILES_TAB : SESSION_TAB
  const tabs = $tabs.get()
  const at = replace === undefined ? -1 : tabs.findIndex(tab => tab.id === replace)

  if (!tabs.some(tab => tab.id === wanted.id)) {
    $tabs.set(at === -1 ? [...tabs, wanted] : tabs.map((tab, index) => (index === at ? wanted : tab)))
  } else if (at !== -1) {
    $tabs.set(tabs.filter(tab => tab.id !== replace))
  }

  focus(wanted.id)
}

/** Open the start page, or focus the one already open. */
export function openGuide(): void {
  const tabs = $tabs.get()

  if (!tabs.some(tab => tab.id === GUIDE_TAB.id)) $tabs.set([...tabs, GUIDE_TAB])

  focus(GUIDE_TAB.id)
}

/**
 * Show `path` in the column, at `line` when the caller knows one.
 *
 * A file already open is revealed rather than duplicated — and still handed the
 * new line, because "show me line 40 of the file I am already looking at" is
 * the request a second click on a tool row makes.
 */
export function openFile(path: string, line?: number): string {
  const id = `text:${path}`
  const tabs = $tabs.get()

  if (!tabs.some(tab => tab.id === id)) {
    $tabs.set([...tabs, { address: path, id, kind: 'text', title: basename(path) }])
  }

  const previous = $navigation.get()[id]

  $navigation.setKey(id, { line, revision: (previous?.revision ?? 0) + 1 })
  focus(id)

  return id
}

/**
 * Close one tab.
 *
 * The neighbour to the left takes focus — the tab the reader most likely came
 * from — and closing the last one reseeds the session tab, because a column
 * with no tabs is a strip of chrome around nothing.
 */
export function closeTab(id: string): void {
  const tabs = $tabs.get()
  const index = tabs.findIndex(tab => tab.id === id)

  if (index === -1) return

  const remaining = tabs.filter(tab => tab.id !== id)

  if (remaining.length === 0) {
    $tabs.set([SESSION_TAB])
    $activeTabId.set(SESSION_TAB.id)
  } else {
    $tabs.set(remaining)

    if ($activeTabId.get() === id) {
      const neighbour = remaining[Math.min(remaining.length - 1, Math.max(0, index - 1))]

      if (neighbour !== undefined) $activeTabId.set(neighbour.id)
    }
  }

  // The tab is gone, so is everything it was holding: a tab reopened for the
  // same path is a new occurrence, with a fresh revision and a fresh read.
  const navigation = { ...$navigation.get() }

  delete navigation[id]
  $navigation.set(navigation)
  forgetTextTab(id)
}

export function focusTab(id: string): void {
  if ($tabs.get().some(tab => tab.id === id)) $activeTabId.set(id)
}

export function toggleFullscreen(): void {
  $fullscreen.set(!$fullscreen.get())
}

/**
 * Close the column.
 *
 * Every way of closing it goes through here, because full screen must not
 * survive a close: reopening would otherwise land the reader in a full-screen
 * panel they never asked for, over a conversation they were reading.
 */
export function closeSidebar(): void {
  $fullscreen.set(false)
  closeDetails()
}

/**
 * Back to the opening shape.
 *
 * Called when the window changes session: the open files belong to *that*
 * session's workspace, and carrying them into another one would show a tree
 * rooted somewhere the reader did not go.
 */
export function resetSidebar(): void {
  $tabs.set([SESSION_TAB])
  $activeTabId.set(SESSION_TAB.id)
  $navigation.set({})
  $fullscreen.set(false)
  resetTextTabs()
  resetTree()
}

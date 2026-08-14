/**
 * Column width preferences, persisted per browser.
 *
 * These are *preferences*, not rendered widths: the solver in
 * `layout/columns.ts` turns them into the widths a frame actually paints, and
 * an auto-collapse never writes back here — which is what lets a re-widened
 * window restore exactly what the user last dragged.
 */

import { atom } from 'nanostores'

import {
  clampWidth,
  DETAILS_MAX,
  DETAILS_MIN,
  SIDEBAR_DEFAULT,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
} from '../layout/columns.ts'

const STORAGE_KEY = 'clawcodex.web.layout'

interface StoredLayout {
  details: number
  sidebar: number
}

function read(): StoredLayout {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)

    if (raw !== null) {
      const parsed = JSON.parse(raw) as Partial<StoredLayout>

      return {
        details: typeof parsed.details === 'number' ? parsed.details : 0,
        sidebar: typeof parsed.sidebar === 'number' ? parsed.sidebar : SIDEBAR_DEFAULT,
      }
    }
  } catch {
    /* unreadable or malformed: fall through to the defaults */
  }

  // Sidebar open at its resting width, details closed — the shape a first-run
  // window should have. Zero means CLOSED for both, so the sidebar's default
  // has to be its width, not zero.
  return { details: 0, sidebar: SIDEBAR_DEFAULT }
}

const initial = read()

export const $sidebarWidth = atom<number>(initial.sidebar)
export const $detailsWidth = atom<number>(initial.details)
/** Manual re-expand override while the viewport is under the breakpoint. */
export const $narrowExpanded = atom<boolean>(false)
export const $narrow = atom<boolean>(false)

function persist(): void {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ details: $detailsWidth.get(), sidebar: $sidebarWidth.get() }),
    )
  } catch {
    /* private mode: the layout holds for this page load */
  }
}

export function setSidebarWidth(px: number): void {
  $sidebarWidth.set(clampWidth(px, SIDEBAR_MIN, SIDEBAR_MAX))
  persist()
}

export function setDetailsWidth(px: number): void {
  $detailsWidth.set(clampWidth(px, DETAILS_MIN, DETAILS_MAX))
  persist()
}

export function toggleSidebar(): void {
  // Under the breakpoint the sidebar is collapsed by derivation, so the toggle
  // flips the manual override instead of the stored preference — otherwise
  // widening the window would surprise the user with a closed sidebar.
  if ($narrow.get()) {
    $narrowExpanded.set(!$narrowExpanded.get())

    return
  }

  $sidebarWidth.set($sidebarWidth.get() === 0 ? SIDEBAR_MIN : 0)
  persist()
}

export function openDetails(): void {
  if ($detailsWidth.get() === 0) setDetailsWidth(360)
}

export function closeDetails(): void {
  $detailsWidth.set(0)
  persist()
}

export function setNarrow(narrow: boolean): void {
  if ($narrow.get() === narrow) return

  $narrow.set(narrow)

  if (!narrow) $narrowExpanded.set(false)
}

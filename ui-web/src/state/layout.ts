/**
 * Column width preferences, persisted per browser.
 *
 * These are *preferences*, not rendered widths: the solver in
 * `layout/columns.ts` turns them into the widths a frame actually paints, and
 * an auto-collapse never writes back here — which is what lets a re-widened
 * window restore exactly what the user last dragged.
 * The trajectory inspector fits its preference inside the conversation in
 * `trajectory/TrajectorySplit.tsx`.
 *
 * `details` is the RIGHT COLUMN, which now holds the tabbed sidebar
 * (`sidebar-right/`) rather than the single details panel it was named for.
 * The name stays because it is a key in the stored payload: renaming it would
 * silently discard the width every existing reader has already dragged. Close
 * the column through `sidebar-right/store.ts`'s `closeSidebar`, not
 * `closeDetails` — full screen has to come down with it.
 */

import { atom, computed } from 'nanostores'

import {
  clampWidth,
  DETAILS_MIN,
  detailsDefault,
  detailsMax,
  SIDEBAR_DEFAULT,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
} from '../layout/columns.ts'

const STORAGE_KEY = 'clawcodex.web.layout'

interface StoredLayout {
  details: number
  sidebar: number
  trajectoryDetails: number
}

export const TRAJECTORY_DETAILS_DEFAULT = 320

function read(): StoredLayout {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)

    if (raw !== null) {
      const parsed = JSON.parse(raw) as Partial<StoredLayout>

      return {
        details: typeof parsed.details === 'number' ? parsed.details : 0,
        sidebar: typeof parsed.sidebar === 'number' ? parsed.sidebar : SIDEBAR_DEFAULT,
        trajectoryDetails:
          typeof parsed.trajectoryDetails === 'number' && Number.isFinite(parsed.trajectoryDetails)
            ? Math.max(240, parsed.trajectoryDetails)
            : TRAJECTORY_DETAILS_DEFAULT,
      }
    }
  } catch {
    /* unreadable or malformed: fall through to the defaults */
  }

  // Sidebar open at its resting width, details closed — the shape a first-run
  // window should have. Zero means CLOSED for both, so the sidebar's default
  // has to be its width, not zero.
  return { details: 0, sidebar: SIDEBAR_DEFAULT, trajectoryDetails: TRAJECTORY_DETAILS_DEFAULT }
}

const initial = read()

export const $sidebarWidth = atom<number>(initial.sidebar)
export const $detailsWidth = atom<number>(initial.details)
export const $trajectoryDetailsWidth = atom<number>(initial.trajectoryDetails)
/**
 * Whether the right column is open — what most of the app actually wants to
 * know. Subscribing to the width itself would re-render a subscriber on every
 * pixel of a drag; this only changes when the column opens or closes.
 */
export const $detailsOpen = computed($detailsWidth, width => width > 0)
/** True while a column handle is held: the frame's drag state, for the rest of the app to read. */
export const $columnDrag = atom<boolean>(false)
/** Manual re-expand override while the viewport is under the breakpoint. */
export const $narrowExpanded = atom<boolean>(false)
export const $narrow = atom<boolean>(false)

function persist(): void {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        details: $detailsWidth.get(), sidebar: $sidebarWidth.get(),
        trajectoryDetails: $trajectoryDetailsWidth.get(),
      }),
    )
  } catch {
    /* private mode: the layout holds for this page load */
  }
}

let persistTimer: ReturnType<typeof setTimeout> | null = null

/** Persist once the writes settle: a drag writes a width per frame, and the storage need not hear every one. */
function persistSoon(): void {
  if (persistTimer !== null) clearTimeout(persistTimer)

  persistTimer = setTimeout(() => {
    persistTimer = null
    persist()
  }, 200)
}

export function setSidebarWidth(px: number): void {
  $sidebarWidth.set(clampWidth(px, SIDEBAR_MIN, SIDEBAR_MAX))
  persistSoon()
}

export function setTrajectoryDetailsWidth(px: number): void {
  if (!Number.isFinite(px)) return

  $trajectoryDetailsWidth.set(Math.max(240, Math.round(px)))
  persistSoon()
}

/** The frame's width when a caller has not measured one: the window's. */
function frameWidth(): number {
  return typeof window === 'undefined' ? 1280 : window.innerWidth
}

/**
 * The right column's ceiling is a share of the frame rather than a number, so
 * the clamp needs the frame's width; the drag passes the one it measured, and
 * a caller without one takes the window's.
 */
export function setDetailsWidth(px: number, viewport = frameWidth()): void {
  $detailsWidth.set(clampWidth(px, DETAILS_MIN, detailsMax(viewport)))
  persistSoon()
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

/** Open the right column at its first-open share of the frame; an open one keeps its width. */
export function openDetails(viewport = frameWidth()): void {
  if ($detailsWidth.get() === 0) setDetailsWidth(detailsDefault(viewport), viewport)
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

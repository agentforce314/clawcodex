import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

import { CheckIcon } from '../icons.tsx'
import css from './Menu.module.css'

/** A selectable row. */
export interface MenuItem {
  danger?: boolean
  disabled?: boolean
  /** Leading glyph. */
  icon?: ReactNode
  id: string
  /** Second line under the label — what the row actually does. */
  hint?: string
  label: ReactNode
}

export interface MenuSeparator {
  id: string
  type: 'separator'
}

export interface MenuLabel {
  id: string
  text: string
  type: 'label'
}

export type MenuEntry = MenuItem | MenuLabel | MenuSeparator

function isSeparator(entry: MenuEntry): entry is MenuSeparator {
  return 'type' in entry && entry.type === 'separator'
}

function isLabel(entry: MenuEntry): entry is MenuLabel {
  return 'type' in entry && entry.type === 'label'
}

/** The list's design height; the stylesheet's cap says the same number. */
export const MENU_MAX_HEIGHT = 420

/** Gap between the anchor and the list (the stylesheet's offset). */
const MENU_GAP = 4

/** Clearance the list keeps from the window's edge. */
const MENU_SAFE_MARGIN = 12

/** Below this much room, the other side is tried before squeezing the list. */
const MENU_MIN_ROOM = 200

/** Never smaller than this: a squeezed list still shows a few rows and its footer. */
const MENU_MIN_HEIGHT = 120

export interface MenuPlacement {
  maxHeight: number
  side: 'bottom' | 'top'
}

/**
 * Where the list goes and how tall it may be, from the room on each side of
 * the anchor.
 *
 * The stylesheet caps the list by the WINDOW's height, which says nothing
 * about where the anchor sits: a list opening below a control in the middle
 * of the window ran past the bottom edge, and with the page itself unable
 * to scroll, its last rows — the pinned "Add workspace…" — were not on
 * screen at all. So the cap is the room on the chosen side, and a side with
 * too little room yields to the other when that one has more.
 */
export function menuPlacement(
  side: 'bottom' | 'top',
  roomAbove: number,
  roomBelow: number,
  cap = MENU_MAX_HEIGHT,
): MenuPlacement {
  const room = (candidate: 'bottom' | 'top') => (candidate === 'top' ? roomAbove : roomBelow)
  const other = side === 'top' ? 'bottom' : 'top'
  const chosen = room(side) < MENU_MIN_ROOM && room(other) > room(side) ? other : side

  return { maxHeight: Math.max(MENU_MIN_HEIGHT, Math.min(cap, room(chosen))), side: chosen }
}

export interface MenuProps {
  /** Alignment against the anchor's edge. */
  align?: 'end' | 'start'
  /** The trigger, rendered in place. */
  anchor: ReactNode
  /**
   * Fill the owner's width: the trigger stretches, and the list spans it —
   * the form-control shape, as opposed to a chip with a list of its own size.
   */
  block?: boolean
  emptyText?: string
  /**
   * Rows pinned below the scrolling list, after a divider: the actions that
   * must stay reachable however long the list is ("Add workspace…"). They
   * are never the selection, and their check is never drawn.
   */
  footer?: readonly MenuItem[]
  items: readonly MenuEntry[]
  onClose: () => void
  onSelect: (id: string) => void
  open: boolean
  selectedId?: string
  /** `top` opens upward — what a control at the bottom of the window needs. */
  side?: 'bottom' | 'top'
}

/**
 * A themed dropdown.
 *
 * This exists because a native `<select>` hands its popup to the OS, which
 * cannot see the app's theme: a dark UI gets a light system list with a
 * system-blue highlight, and no stylesheet reaches inside it. Owning the list
 * is the only way the two agree.
 *
 * Controlled: the owner holds `open`, so the trigger's pressed state and the
 * list can never disagree about whether the menu is showing.
 */
export function Menu({
  align = 'start',
  anchor,
  block = false,
  emptyText,
  footer,
  items,
  onClose,
  onSelect,
  open,
  selectedId,
  side = 'bottom',
}: MenuProps) {
  const root = useRef<HTMLSpanElement | null>(null)
  // Measured while open; null until then (and in a layout-less test DOM,
  // where an unmeasurable anchor keeps the requested side and the
  // stylesheet's cap).
  const [placement, setPlacement] = useState<MenuPlacement | null>(null)

  useLayoutEffect(() => {
    if (!open) {
      setPlacement(null)

      return
    }

    const measure = () => {
      const rect = root.current?.getBoundingClientRect()

      if (rect === undefined || (rect.width === 0 && rect.height === 0)) return

      setPlacement(
        menuPlacement(
          side,
          rect.top - MENU_GAP - MENU_SAFE_MARGIN,
          window.innerHeight - rect.bottom - MENU_GAP - MENU_SAFE_MARGIN,
        ),
      )
    }

    measure()
    window.addEventListener('resize', measure)

    return () => {
      window.removeEventListener('resize', measure)
    }
  }, [open, side])

  useEffect(() => {
    if (!open) return

    const onPointerDown = (event: PointerEvent) => {
      if (root.current?.contains(event.target as Node) === true) return

      onClose()
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        // Stopped so a menu inside a dialog closes only the menu.
        event.stopPropagation()
        onClose()
      }
    }

    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose, open])

  const selectable = items.filter((entry): entry is MenuItem => !isSeparator(entry) && !isLabel(entry))

  const row = (entry: MenuItem, selected: boolean) => (
    <button
      className={[css.item, entry.danger === true ? css.danger : ''].filter(Boolean).join(' ')}
      disabled={entry.disabled}
      key={entry.id}
      onClick={() => {
        onSelect(entry.id)
      }}
      role="menuitem"
      type="button"
    >
      {entry.icon !== undefined && <span className={css.itemIcon}>{entry.icon}</span>}
      <span className={css.itemBody}>
        <span className={css.itemLabel}>{entry.label}</span>
        {entry.hint !== undefined && <span className={css.itemHint}>{entry.hint}</span>}
      </span>
      {selected && <CheckIcon className={css.check} size={14} />}
    </button>
  )

  const resolvedSide = placement?.side ?? side

  return (
    <span className={[css.root, block ? css.rootBlock : ''].filter(Boolean).join(' ')} ref={root}>
      {anchor}
      {open && (
        <div
          className={[
            css.list,
            resolvedSide === 'top' ? css.sideTop : css.sideBottom,
            align === 'end' ? css.alignEnd : css.alignStart,
            block ? css.listBlock : '',
          ]
            .filter(Boolean)
            .join(' ')}
          role="menu"
          style={placement === null ? undefined : { maxHeight: placement.maxHeight }}
        >
          <div className={css.viewport}>
            {selectable.length === 0 && emptyText !== undefined ? (
              <div className={css.empty}>{emptyText}</div>
            ) : (
              items.map(entry => {
                if (isSeparator(entry)) {
                  return <div className={css.separator} key={entry.id} role="separator" />
                }

                if (isLabel(entry)) {
                  return (
                    <div className={css.label} key={entry.id} role="presentation">
                      {entry.text}
                    </div>
                  )
                }

                return row(entry, entry.id === selectedId)
              })
            )}
          </div>
          {footer !== undefined && footer.length > 0 && (
            // Outside the viewport on purpose: the list scrolls, the footer
            // does not, so its rows stay in reach at any list length.
            <div className={css.footer}>
              <div className={css.separator} role="separator" />
              {footer.map(entry => row(entry, false))}
            </div>
          )}
        </div>
      )}
    </span>
  )
}

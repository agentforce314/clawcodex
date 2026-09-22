import { useEffect, useRef, type ReactNode } from 'react'

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

  return (
    <span className={[css.root, block ? css.rootBlock : ''].filter(Boolean).join(' ')} ref={root}>
      {anchor}
      {open && (
        <div
          className={[
            css.list,
            side === 'top' ? css.sideTop : css.sideBottom,
            align === 'end' ? css.alignEnd : css.alignStart,
            block ? css.listBlock : '',
          ]
            .filter(Boolean)
            .join(' ')}
          role="menu"
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

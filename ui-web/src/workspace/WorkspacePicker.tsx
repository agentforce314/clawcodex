import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'

import type { DirectoryEntry, DirectoryListing } from '../gateway/protocol.ts'
import { listDirectory } from '../state/actions.ts'
import { Button } from '../ui/primitives/Button.tsx'
import { ChevronRightIcon, FolderIcon, SearchIcon, XIcon } from '../ui/icons.tsx'
import css from './WorkspacePicker.module.css'

export interface WorkspacePickerProps {
  /** Label for the confirm action; differs when a session is already running. */
  confirmLabel: string
  onClose: () => void
  onPick: (path: string) => void
  /** Directory to open on: the current workspace, or home when absent. */
  startPath?: string
}

/**
 * Browse the server's filesystem to choose where a session runs.
 *
 * The server's, not the browser's: a File System Access handle is opaque, and
 * the agent needs a real path it can `cd` into. Every path in here comes from
 * the backend for the same reason — the client never joins segments itself.
 *
 * Two distinct gestures on purpose: clicking a row *descends into* it, and the
 * footer button *chooses the folder you are looking at*. Collapsing those into
 * one makes picking a directory that has children impossible without an
 * awkward modifier.
 */
export function WorkspacePicker({
  confirmLabel,
  onClose,
  onPick,
  startPath,
}: WorkspacePickerProps) {
  const [listing, setListing] = useState<DirectoryListing | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [showHidden, setShowHidden] = useState(false)
  const [active, setActive] = useState(0)
  const root = useRef<HTMLDivElement | null>(null)
  const filterInput = useRef<HTMLInputElement | null>(null)
  // Guards against a slow listing landing after a newer one.
  const request = useRef(0)

  const open = useCallback((path?: string) => {
    const ticket = ++request.current
    setLoading(true)
    setError('')

    void listDirectory(path)
      .then(next => {
        if (request.current !== ticket) return

        setListing(next)
        setFilter('')
        setActive(0)
      })
      .catch((cause: unknown) => {
        if (request.current !== ticket) return

        // Surfaced, never swallowed: an unreadable directory rendered as an
        // empty folder sends the user hunting for a project that is there.
        setError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => {
        if (request.current === ticket) setLoading(false)
      })
  }, [])

  useEffect(() => {
    open(startPath)
  }, [open, startPath])

  useEffect(() => {
    filterInput.current?.focus()
  }, [])

  // Dismissal: click outside or Escape, the two gestures people already use.
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (root.current?.contains(event.target as Node) === true) return

      onClose()
    }

    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
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
  }, [onClose])

  const rows = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    const entries = listing?.entries ?? []

    return entries.filter(entry => {
      if (!showHidden && entry.hidden && !needle) return false

      return needle === '' || entry.name.toLowerCase().includes(needle)
    })
  }, [filter, listing, showHidden])

  useEffect(() => {
    setActive(0)
  }, [rows.length])

  const onFilterKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActive(index => Math.min(rows.length - 1, index + 1))

        return
      }

      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActive(index => Math.max(0, index - 1))

        return
      }

      if (event.key === 'Enter') {
        event.preventDefault()

        const entry = rows[active]

        // Enter with no rows confirms the folder you are in — the natural
        // outcome when the filter has narrowed everything away.
        if (entry === undefined) {
          if (listing !== null) onPick(listing.path)

          return
        }

        open(entry.path)
      }
    },
    [active, listing, onPick, open, rows],
  )

  const crumbs = listing?.crumbs ?? []

  return (
    <div className={css.root} ref={root} role="dialog">
      <div className={css.header}>
        <FolderIcon size={14} />
        <span className={css.title}>Choose a folder</span>
        <button
          aria-label="Close"
          className={css.iconButton}
          onClick={onClose}
          type="button"
        >
          <XIcon size={12} />
        </button>
      </div>

      <div className={css.crumbs}>
        {crumbs.map((crumb, index) => (
          <span key={crumb.path}>
            {index > 0 && <span className={css.crumbSep}>›</span>}
            <button
              className={[css.crumb, index === crumbs.length - 1 ? css.crumbCurrent : '']
                .filter(Boolean)
                .join(' ')}
              onClick={() => {
                open(crumb.path)
              }}
              type="button"
            >
              {crumb.name}
            </button>
          </span>
        ))}
      </div>

      <div className={css.filter}>
        <SearchIcon size={12} />
        <input
          aria-label="Filter folders"
          className={css.filterInput}
          onChange={event => {
            setFilter(event.currentTarget.value)
          }}
          onKeyDown={onFilterKeyDown}
          placeholder="Filter — ↑↓ to move, Enter to open"
          ref={filterInput}
          value={filter}
        />
      </div>

      <div className={css.list}>
        {error !== '' ? (
          <div className={[css.status, css.error].join(' ')}>{error}</div>
        ) : loading ? (
          <div className={css.status}>Loading…</div>
        ) : rows.length === 0 ? (
          <div className={css.status}>
            {filter === '' ? 'No subfolders here.' : 'Nothing matches that filter.'}
          </div>
        ) : (
          rows.map((entry: DirectoryEntry, index) => (
            <button
              className={[css.row, entry.hidden ? css.rowHidden : ''].filter(Boolean).join(' ')}
              data-active={index === active ? 'true' : undefined}
              key={entry.path}
              onClick={() => {
                open(entry.path)
              }}
              onPointerEnter={() => {
                setActive(index)
              }}
              type="button"
            >
              <FolderIcon className={css.rowIcon} size={13} />
              <span className={css.rowName}>{entry.name}</span>
              <ChevronRightIcon className={css.rowChevron} size={12} />
            </button>
          ))
        )}
        {listing?.truncated === true && (
          <div className={css.status}>
            Showing the first {listing.entries.length} folders — filter to narrow.
          </div>
        )}
      </div>

      <div className={css.footer}>
        <button
          aria-pressed={showHidden}
          className={css.toggleHidden}
          onClick={() => {
            setShowHidden(value => !value)
          }}
          type="button"
        >
          {showHidden ? 'Hide dotfiles' : 'Show dotfiles'}
        </button>
        <span className={css.currentPath} title={listing?.path}>
          {listing?.path ?? ''}
        </span>
        <Button
          disabled={listing === null}
          onClick={() => {
            if (listing !== null) onPick(listing.path)
          }}
          size="sm"
          variant="primary"
        >
          {confirmLabel}
        </Button>
      </div>
    </div>
  )
}

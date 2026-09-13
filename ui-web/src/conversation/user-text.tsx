/**
 * A sent message's text, with its file references as chips.
 *
 * What the user typed is shown as typed — plain text, never markdown — except
 * that an `@path` mention becomes a chip carrying the file's type icon and its
 * name, and opens the file in the right column on a click. Adapted from the
 * reference client's `projectUserText`: a folder reference (a trailing slash)
 * keeps the chip but not the click, since a directory has no page to open.
 *
 * The mention grammar is the composer's own (`mentions.ts`): an `@` opening a
 * word, running to the next whitespace, or a quoted path with spaces in it.
 */

import { type ReactNode } from 'react'

import { FileTypeIcon } from '../ui/primitives/FileTypeIcon.tsx'
import css from './user-text.module.css'

const MENTION = /(^|\s)@(?:"([^"\n]+)"|([^\s@"]+))/g

export interface UserTextOptions {
  /** Open one file; absent when there is nowhere to open it. */
  onOpen?: (path: string) => void
  /** The session's root, which a relative mention is read against. */
  workspace?: string
}

/** The mention's path as the backend would read it. */
export function resolveMention(path: string, workspace: string | undefined): string {
  if (path.startsWith('/') || /^[a-zA-Z]:[\\/]/.test(path) || workspace === undefined || workspace === '') {
    return path
  }

  return `${workspace.replace(/[/\\]$/, '')}/${path}`
}

/** The last path segment — what the chip shows of a path. */
function mentionLabel(path: string): string {
  const segments = path.split(/[/\\]/).filter(Boolean)

  return segments.at(-1) ?? path
}

export function projectUserText(text: string, options: UserTextOptions = {}): ReactNode {
  const parts: ReactNode[] = []
  let cursor = 0

  MENTION.lastIndex = 0

  for (const match of text.matchAll(MENTION)) {
    const lead = match[1] ?? ''
    const path = match[2] ?? match[3] ?? ''
    const start = match.index + lead.length
    const end = match.index + match[0].length

    if (start > cursor) parts.push(text.slice(cursor, start))

    const folder = path.endsWith('/')
    const label = mentionLabel(path)
    const body = (
      <>
        <FileTypeIcon
          className={css.icon}
          kind={folder ? 'folder' : 'file'}
          path={path}
          size={14}
        />
        {label}
      </>
    )

    if (folder || options.onOpen === undefined) {
      parts.push(
        <span className={css.chip} data-ref-chip={folder ? 'folder' : 'file'} key={start} title={path}>
          {body}
        </span>,
      )
    } else {
      const onOpen = options.onOpen
      const resolved = resolveMention(path, options.workspace)

      parts.push(
        <button
          className={[css.chip, css.openable].join(' ')}
          data-ref-chip="file"
          key={start}
          onClick={event => {
            // A double-click, or a click that settles a text selection, is
            // selecting, not opening.
            if (event.detail > 1) return
            if (event.currentTarget.ownerDocument.getSelection()?.isCollapsed === false) return

            onOpen(resolved)
          }}
          title={resolved}
          type="button"
        >
          {body}
        </button>,
      )
    }

    cursor = end
  }

  if (parts.length === 0) return text

  if (cursor < text.length) parts.push(text.slice(cursor))

  return parts
}

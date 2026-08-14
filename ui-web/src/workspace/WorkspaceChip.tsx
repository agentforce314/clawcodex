import { useStore } from '@nanostores/react'
import { useCallback, useState } from 'react'

import { chooseWorkspace } from '../state/actions.ts'
import { $sessionId, $workspace } from '../state/store.ts'
import { ChevronDownIcon, FolderIcon } from '../ui/icons.tsx'
import { WorkspacePicker } from './WorkspacePicker.tsx'
import css from './WorkspaceChip.module.css'

export interface WorkspaceChipProps {
  /** `hero` is the pill under the headline; `crumb` is the header path. */
  variant?: 'crumb' | 'hero'
}

/** Enough of a path to identify the folder: its last two segments. */
function shortPath(path: string): string {
  const segments = path.split(/[/\\]/).filter(Boolean)

  return segments.length <= 2 ? path : segments.slice(-2).join('/')
}

/**
 * The current workspace, and the way to change it.
 *
 * Both call sites open the same picker. The confirm label differs because the
 * consequence does: with no session yet the choice simply aims the next one,
 * while a running session's working directory is fixed at spawn, so choosing a
 * new folder starts a session there.
 */
export function WorkspaceChip({ variant = 'hero' }: WorkspaceChipProps) {
  const workspace = useStore($workspace)
  const sessionId = useStore($sessionId)
  const [open, setOpen] = useState(false)

  const onPick = useCallback((path: string) => {
    setOpen(false)
    void chooseWorkspace(path)
  }, [])

  const close = useCallback(() => {
    setOpen(false)
  }, [])

  if (workspace === '') return null

  return (
    <span className={css.root}>
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        className={variant === 'hero' ? css.chip : css.crumb}
        onClick={() => {
          setOpen(value => !value)
        }}
        title={`${workspace} — click to change`}
        type="button"
      >
        <FolderIcon className={css.icon} size={variant === 'hero' ? 14 : 13} />
        <span className={css.label}>
          {variant === 'hero' ? shortPath(workspace) : workspace}
        </span>
        <ChevronDownIcon className={css.chevron} size={11} />
      </button>
      {open && (
        <span className={css.popover}>
          <WorkspacePicker
            confirmLabel={sessionId === null ? 'Use this folder' : 'Start a session here'}
            onClose={close}
            onPick={onPick}
            startPath={workspace}
          />
        </span>
      )}
    </span>
  )
}

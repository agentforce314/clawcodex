import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'

import { createSession } from '../state/actions.ts'
import { $newSessionDialog, $projects, $workspace } from '../state/store.ts'
import { Button } from '../ui/primitives/Button.tsx'
import { Menu, type MenuEntry, type MenuItem } from '../ui/primitives/Menu.tsx'
import { ChevronDownIcon, FolderIcon, PlusIcon, XIcon } from '../ui/icons.tsx'
import css from './NewSessionDialog.module.css'

/** The picker choice that reveals the folder-path field. */
export const NEW_WORKSPACE = '__new_workspace__'

/** The last path segment, for a label; the whole path when it has none. */
export function baseName(path: string): string {
  const segments = path.split(/[/\\]/).filter(Boolean)

  return segments[segments.length - 1] ?? path
}

/** The shape of a sidebar project this dialog reads: its path and its lanes'. */
export interface WorkspaceSource {
  path?: string | null
  repos?: readonly { groups?: readonly { path?: string | null }[] }[]
}

/**
 * The workspaces the dialog offers: the current one first, then every folder
 * the sidebar knows a session in — a repo and each of its worktree lanes,
 * which are the natural places to start another session — without repeats.
 * "Home" (sessions with no folder) has no path to start a session in, so it
 * is not a choice.
 */
export function knownWorkspaces(current: string, projects: readonly WorkspaceSource[]): string[] {
  const seen = new Set<string>()
  const paths: string[] = []
  const candidates = [
    current,
    ...projects.flatMap(project => [
      project.path ?? '',
      ...(project.repos ?? []).flatMap(repo => (repo.groups ?? []).map(lane => lane.path ?? '')),
    ]),
  ]

  for (const path of candidates) {
    if (path === '' || seen.has(path)) continue

    seen.add(path)
    paths.push(path)
  }

  return paths
}

/**
 * The picker's rows: one per workspace, named by its folder. The full path
 * rides along as the second line only where two folders share a name — the
 * common case stays one compact line per row, and a `clawcodex` next to
 * another `clawcodex` still tells them apart.
 */
export function workspaceRows(workspaces: readonly string[]): MenuEntry[] {
  const counts = new Map<string, number>()

  for (const path of workspaces) {
    const name = baseName(path)
    counts.set(name, (counts.get(name) ?? 0) + 1)
  }

  return workspaces.map(path => {
    const name = baseName(path)

    return {
      icon: <FolderIcon size={14} />,
      id: path,
      label: name,
      ...((counts.get(name) ?? 0) > 1 && { hint: path }),
    }
  })
}

/**
 * Pinned below the list, after a divider, so it is in reach however many
 * workspaces there are: at the end of a long list it was the row nobody
 * scrolled to.
 */
const ADD_WORKSPACE: MenuItem[] = [{ icon: <PlusIcon size={14} />, id: NEW_WORKSPACE, label: 'Add workspace…' }]

export function openNewSessionDialog(): void {
  $newSessionDialog.set(true)
}

export function closeNewSessionDialog(): void {
  $newSessionDialog.set(false)
}

/**
 * The New session dialog: which workspace, or a new one, and whether to
 * isolate the session in a git worktree.
 *
 * A session runs somewhere, and until now the only somewhere was the current
 * workspace: starting work in another project meant browsing to it first.
 * The dialog puts the choice where the intent is. The workspace picker lists
 * every folder the sidebar knows, with **Add workspace…** pinned below the
 * list; that takes an absolute path and makes the folder if it is not there
 * yet. The worktree switch runs the session in a fresh checkout of the repo,
 * the CLI's `--worktree`, so parallel sessions cannot step on each other's
 * files. Errors stay in the dialog: a path the backend refuses is corrected
 * here, not read off a status line behind a closed dialog.
 */
export function NewSessionDialog() {
  const open = useStore($newSessionDialog)

  if (!open) return null

  return <NewSessionForm />
}

function NewSessionForm() {
  const workspace = useStore($workspace)
  const projects = useStore($projects)
  const workspaces = useMemo(() => knownWorkspaces(workspace, projects), [projects, workspace])
  const rows = useMemo(() => workspaceRows(workspaces), [workspaces])
  const [choice, setChoice] = useState(() => workspaces[0] ?? NEW_WORKSPACE)
  const [menuOpen, setMenuOpen] = useState(false)
  const [path, setPath] = useState('')
  const [worktree, setWorktree] = useState(false)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)

  // Read by the document-level handlers below, which must know the menu's
  // state at the moment of the key or the press, not at their registration.
  const menuOpenRef = useRef(menuOpen)
  menuOpenRef.current = menuOpen

  const close = closeNewSessionDialog

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      // With the picker open, Escape is the picker's: its own listener runs
      // after this one and closes just the menu.
      if (event.key === 'Escape' && !menuOpenRef.current) {
        event.stopPropagation()
        closeNewSessionDialog()
      }
    }

    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [])

  // The scrim closes on a click that STARTED on it: a drag that begins in
  // the path field and ends outside must not throw the form away. A press
  // that lands while the picker is open only closes the picker — this
  // handler runs before the picker's document listener does, so it still
  // sees the menu open.
  const pressedOnScrim = useRef(false)

  const creatingNew = choice === NEW_WORKSPACE
  const target = creatingNew ? path.trim() : choice
  const canCreate = target !== '' && !creating

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()

    if (!canCreate) return

    setCreating(true)
    setError('')

    const failure = await createSession({
      cwd: target,
      ...(creatingNew && { createDir: true }),
      ...(worktree && { worktree: true }),
    })

    setCreating(false)

    if (failure === null) close()
    else setError(failure)
  }

  return (
    <div
      className={css.scrim}
      onClick={event => {
        if (pressedOnScrim.current && event.target === event.currentTarget) close()

        pressedOnScrim.current = false
      }}
      onPointerDown={event => {
        pressedOnScrim.current = event.target === event.currentTarget && !menuOpen
      }}
    >
      <form
        aria-labelledby="cc-new-session-title"
        className={css.dialog}
        onClick={event => {
          event.stopPropagation()
        }}
        onSubmit={event => {
          void onSubmit(event)
        }}
        role="dialog"
      >
        <div className={css.head}>
          <span className={css.title} id="cc-new-session-title">
            New session
          </span>
          <button aria-label="Close" className={css.close} onClick={close} type="button">
            <XIcon size={12} />
          </button>
        </div>

        <div className={css.field}>
          <span className={css.label} id="cc-new-session-workspace">
            Workspace
          </span>
          <Menu
            anchor={
              <button
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                aria-labelledby="cc-new-session-workspace"
                autoFocus={!creatingNew}
                className={css.picker}
                onClick={() => {
                  setMenuOpen(value => !value)
                }}
                type="button"
              >
                <span className={css.pickerIcon}>
                  {creatingNew ? <PlusIcon size={14} /> : <FolderIcon size={14} />}
                </span>
                <span className={css.pickerName}>{creatingNew ? 'New workspace' : baseName(choice)}</span>
                {!creatingNew && (
                  <span className={css.pickerPath} title={choice}>
                    {choice}
                  </span>
                )}
                <ChevronDownIcon className={css.pickerChevron} size={12} />
              </button>
            }
            block
            emptyText="No workspaces yet."
            footer={ADD_WORKSPACE}
            items={rows}
            onClose={() => {
              setMenuOpen(false)
            }}
            onSelect={id => {
              setChoice(id)
              setMenuOpen(false)
              setError('')
            }}
            open={menuOpen}
            selectedId={creatingNew ? undefined : choice}
          />
        </div>

        {creatingNew && (
          <label className={css.field}>
            <span className={css.label}>Folder path</span>
            <input
              autoFocus
              className={css.input}
              onChange={event => {
                setPath(event.currentTarget.value)
                setError('')
              }}
              placeholder="/absolute/path/to/project"
              spellCheck={false}
              type="text"
              value={path}
            />
            <span className={css.hint}>Created if it does not exist yet.</span>
          </label>
        )}

        <label className={css.switchRow}>
          <span className={css.switchText}>
            <span className={css.label}>Worktree</span>
            <span className={css.hint}>Isolate this session in its own git worktree</span>
          </span>
          <input
            checked={worktree}
            className={css.switch}
            onChange={event => {
              setWorktree(event.currentTarget.checked)
            }}
            role="switch"
            type="checkbox"
          />
        </label>

        {error !== '' && (
          <div className={css.error} role="alert">
            {error}
          </div>
        )}

        <div className={css.actions}>
          <Button onClick={close} size="sm" variant="outline">
            Cancel
          </Button>
          <Button disabled={!canCreate} size="sm" type="submit" variant="primary">
            {creating ? 'Creating…' : 'Create'}
          </Button>
        </div>
      </form>
    </div>
  )
}

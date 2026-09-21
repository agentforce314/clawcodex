import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'

import { createSession } from '../state/actions.ts'
import { $newSessionDialog, $projects, $workspace } from '../state/store.ts'
import { Button } from '../ui/primitives/Button.tsx'
import { XIcon } from '../ui/icons.tsx'
import css from './NewSessionDialog.module.css'

/** The select value that reveals the folder-path field. */
export const NEW_WORKSPACE = '__new_workspace__'

/** The last path segment, for a label; the whole path when it has none. */
function baseName(path: string): string {
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
 * The dialog puts the choice where the intent is. "Create new workspace…"
 * takes an absolute path and makes the folder if it is not there yet; the
 * worktree switch runs the session in a fresh checkout of the repo, the
 * CLI's `--worktree`, so parallel sessions cannot step on each other's files.
 * Errors stay in the dialog: a path the backend refuses is corrected here,
 * not read off a status line behind a closed dialog.
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
  const [choice, setChoice] = useState(() => workspaces[0] ?? NEW_WORKSPACE)
  const [path, setPath] = useState('')
  const [worktree, setWorktree] = useState(false)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)

  const close = closeNewSessionDialog

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        closeNewSessionDialog()
      }
    }

    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [])

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
    <div className={css.scrim} onClick={close}>
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

        <label className={css.field}>
          <span className={css.label}>Workspace</span>
          <select
            autoFocus={!creatingNew}
            className={css.select}
            onChange={event => {
              setChoice(event.currentTarget.value)
              setError('')
            }}
            value={choice}
          >
            {workspaces.map(candidate => (
              <option key={candidate} value={candidate}>
                {baseName(candidate)} — {candidate}
              </option>
            ))}
            <option value={NEW_WORKSPACE}>Create new workspace…</option>
          </select>
        </label>

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
